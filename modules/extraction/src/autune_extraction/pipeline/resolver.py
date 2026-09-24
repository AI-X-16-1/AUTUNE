"""The reference resolver: in-process weights, our own inference server, a fake.

Same three implementations as ``pipeline.classifier``, same reason -- no
external-API option, because handing a commitment's context to somebody else's
model is a decision about where personal data goes (privacy.md section 6), not
a value of ``AUTUNE_EXTRACTION_RESOLVER_IMPL``.

``torch`` and ``transformers`` are imported inside the class that needs them,
same reason as ``LocalDeberta``: importing at module scope would make
``apps/api`` load a generation stack to serve a health check.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from autune_core import get_logger
from autune_integrations.errors import TransientIntegrationError

from .base import ResolutionRequest
from .classifier import RETRY_BACKOFF_SEC

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

MAX_CONTEXT_UTTERANCES = 4
"""How many preceding utterances a target may draw a referent from (#175's own
design). A decision or commitment's antecedent -- who "그거" or "우리 팀" is --
is almost always in the sentence or two just before it; a window this size
covers that without handing the model the whole meeting to resolve one line."""


_DIGIT_RUN = re.compile(r"\d+")
"""What ``_grounded`` checks did not appear from nowhere.

Not every possible invention -- a resolver can still substitute the wrong
*name* for a pronoun and this would not catch it. It catches the costliest
class cheaply and without another model: a date, a count or an amount that is
not anywhere in the window is not a resolved reference, it is a new fact, and
#175's own design refuses exactly that ("창에 없는 사실을 만들지 않음")."""


def _grounded(resolved: str, window: str) -> bool:
    """Every digit run ``resolved`` states also appears somewhere in ``window``.

    ``window`` is the target and its context joined, so a number the target
    utterance itself already said is never flagged -- only one the resolver
    introduced that the window never mentioned.
    """
    return all(digits in window for digits in _DIGIT_RUN.findall(resolved))


def _window_text(request: ResolutionRequest) -> str:
    return "\n".join((*request.context, request.target))


class FakeResolver:
    """No weights, no network, deterministic: the target, unchanged.

    What the pipeline runs before a resolver model exists and what tests run
    against. Not an approximation of resolution quality -- callers get exactly
    the same raw quote ``build_action_items`` already wrote before #175, so
    everything downstream of resolution can be built against this seam before
    the model behind it does anything.
    """

    model_version = "fake"

    def resolve(self, requests: list[ResolutionRequest]) -> list[str]:
        return [request.target for request in requests]


_PROMPT_TEMPLATE = """\
다음은 회의 중 연속된 발화 목록입니다 (오래된 순).

{context}
--- 해소 대상 ---
{target}

"해소 대상" 문장에서 "그거", "그건", "저희 팀", "이거" 같은 대명사·생략된 지시어를 \
위 발화들이 실제로 가리키는 대상으로 바꿔 한 문장으로 다시 쓰세요.

규칙:
- 위 발화에 없는 새로운 사실(날짜, 숫자, 이름 등)을 만들어내지 마세요.
- 마스킹된 토큰(예: 대괄호로 묶인 표현)은 그대로 두세요.
- 위 발화들로 풀리지 않으면 "해소 대상" 문장을 그대로 반환하세요.
- 다시 쓴 문장 하나만 출력하고, 다른 설명은 붙이지 마세요.
"""


def _prompt(request: ResolutionRequest) -> str:
    context = "\n".join(request.context) if request.context else "(없음)"
    return _PROMPT_TEMPLATE.format(context=context, target=request.target)


class LocalQwenResolver:
    """Weights in this process. ``Qwen/Qwen3-4B-Instruct-2507`` (Apache-2.0),
    #175's own starting candidate -- confirmed or replaced by the Korean
    judgment run the issue asks for, not assumed here.

    Loaded once per process, same as ``LocalDeberta``: a 4B-parameter model
    reloaded per call would dominate the pipeline far more than a classifier
    forward pass does.
    """

    def __init__(self, checkpoint: str, *, device: str = "cpu", max_new_tokens: int = 96) -> None:
        if not checkpoint:
            raise ValueError("LocalQwenResolver needs a checkpoint")
        self._checkpoint = checkpoint
        self._device = device
        self._max_new_tokens = max_new_tokens
        self._model: Any = None
        self._tokenizer: Any = None

    @property
    def model_version(self) -> str:
        return self._checkpoint

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch  # noqa: PLC0415
            from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "the local resolver needs the 'local-models' extra: "
                "uv sync --package autune-extraction --extra local-models"
            ) from exc

        if self._device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError(
                "AUTUNE_EXTRACTION_RESOLVER_DEVICE=cuda but torch reports no CUDA "
                f"device (torch {torch.__version__}). See docs/engineering/environments.md."
            )

        self._torch = torch
        dtype = torch.bfloat16 if self._device == "cuda" else torch.float32
        self._tokenizer = AutoTokenizer.from_pretrained(self._checkpoint)
        self._model = AutoModelForCausalLM.from_pretrained(self._checkpoint, torch_dtype=dtype)
        self._model.to(self._device)
        self._model.eval()
        log.info(
            "extraction_resolver_loaded", checkpoint=self._checkpoint, device=self._device
        )

    def _generate(self, request: ResolutionRequest) -> str:
        torch = self._torch
        messages = [{"role": "user", "content": _prompt(request)}]
        text = self._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        encoded = self._tokenizer(text, return_tensors="pt").to(self._device)
        with torch.no_grad():
            output = self._model.generate(
                **encoded,
                max_new_tokens=self._max_new_tokens,
                do_sample=False,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        generated = output[0][encoded["input_ids"].shape[1] :]
        return self._tokenizer.decode(generated, skip_special_tokens=True).strip()

    def resolve(self, requests: list[ResolutionRequest]) -> list[str]:
        if not requests:
            return []
        self._load()
        resolved = []
        failures = 0
        for request in requests:
            try:
                answer = self._generate(request)
            except Exception:  # noqa: BLE001 - #175: one bad generation must not fail the meeting
                log.warning("extraction_resolver_generation_failed", exc_info=True)
                resolved.append(request.target)
                failures += 1
                continue
            if not answer or not _grounded(answer, _window_text(request)):
                resolved.append(request.target)
                failures += 1
                continue
            resolved.append(answer)
        if failures:
            log.info(
                "extraction_resolver_fallback", requests=len(requests), fallbacks=failures
            )
        return resolved


def _resolver_client(endpoint: str) -> Any:
    """Our inference server's resolver endpoint, as a client -- same pattern as
    ``classifier._classifier_client``, its own class because ``addressing``
    and ``service`` are declared per class, not per instance."""
    from autune_integrations.base import HttpClient  # noqa: PLC0415

    class ResolverClient(HttpClient):
        service = "extraction-resolver"

    return ResolverClient(endpoint)


class HostedResolver:
    """The same model on our own inference server, same shape as
    ``HostedDeberta``: goes through ``autune_integrations.HttpClient`` so the
    outbound guard runs, and each call is re-attempted on a transient failure.

    One request per resolution rather than a batch: a meeting has tens of
    commitments, not hundreds of utterances, so there is no #113-shaped problem
    here yet to design bounded concurrency around.
    """

    def __init__(self, endpoint: str, model_version: str) -> None:
        self._client = _resolver_client(endpoint)
        self._model_version = model_version

    @property
    def model_version(self) -> str:
        return self._model_version

    def _post(self, request: ResolutionRequest) -> Any:
        body = {"target": request.target, "context": list(request.context)}
        for attempt, wait in enumerate(RETRY_BACKOFF_SEC, start=1):
            try:
                return self._client.request("POST", "/resolve", json=body)
            except TransientIntegrationError as exc:
                log.info(
                    "extraction_resolver_retry",
                    attempt=attempt,
                    reason=str(exc),
                )
                time.sleep(wait)
        return self._client.request("POST", "/resolve", json=body)

    def resolve(self, requests: list[ResolutionRequest]) -> list[str]:
        resolved = []
        for request in requests:
            try:
                body = self._post(request)
            except Exception:  # noqa: BLE001 - #175: one bad call must not fail the meeting
                log.warning("extraction_resolver_call_failed", exc_info=True)
                resolved.append(request.target)
                continue
            answer = body.get("resolved") if isinstance(body, dict) else None
            if not isinstance(answer, str) or not answer or not _grounded(
                answer, _window_text(request)
            ):
                resolved.append(request.target)
                continue
            resolved.append(answer)
        return resolved
