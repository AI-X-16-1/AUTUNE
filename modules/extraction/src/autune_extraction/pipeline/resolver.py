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

from .base import Embedder, ResolutionRequest
from .classifier import RETRY_BACKOFF_SEC

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

MAX_CONTEXT_UTTERANCES = 4
"""How many preceding utterances a target may draw a referent from (#175's own
design). A decision or commitment's antecedent -- who "그거" or "우리 팀" is --
is almost always in the sentence or two just before it; a window this size
covers that without handing the model the whole meeting to resolve one line."""

MAX_CONTEXT_AFTER = 2
"""How many following utterances a target may also draw on. Smaller than the
preceding window -- a clarifying exchange right after a commitment ("그게
언제까지였죠?" / "다음 주 화요일이요") is usually one turn, not several -- and
worth having at all only because resolution runs over a finished transcript,
never live."""


_DIGIT_RUN = re.compile(r"\d+")
"""A date, a count or an amount the resolved sentence states. Cheap to check
without another model: not anywhere in the window means it is a new fact, and
#175's own design refuses exactly that ("창에 없는 사실을 만들지 않음")."""

_NAMED_PERSON = re.compile(r"[가-힣]{2,4}(?:님|씨)")
"""A person named by an honorific, the way a colleague is addressed in speech
("박지영님", "이건우씨"). Checked for the same reason as ``_DIGIT_RUN`` and
raised in review of #366: names are not masked the way phone numbers and
emails are (there is no pattern to detect one by), so a real name can sit in
plain text in the window, and a resolver substituting the *wrong* one for a
pronoun is not caught by a digit check at all. This is the more expensive
mistake -- ``assignee_id`` still comes from ``speaker_id``, never from this
resolved text (see ``resolve_commitment_references``), so a wrong name here
means the card shows two different people, not one who might be right."""

_FOREIGN_SCRIPT_RUN = re.compile(r"[぀-ヿ]+")
"""A run of Japanese hiragana or katakana. Never legitimate in this module's
all-Korean transcripts, so a run the window never had is a decoding artifact
from a multilingually-pretrained model, not a resolution. Live case from
#366's own dummy-transcript comparison (a synthetic, non-AI-Hub meeting): with
an all-Korean prompt and an all-Korean context window, Qwen3-4B still produced
"...정산 주기について 설명하겠습니다" -- no invented number or name, so
``_DIGIT_RUN``/``_NAMED_PERSON`` alone would have let it through."""


def _leaks_foreign_script(resolved: str, window: str) -> bool:
    """Whether ``resolved`` contains hiragana/katakana the window never had.

    A standalone predicate, not folded silently into ``_grounded``, because
    ``LocalQwenResolver.resolve`` needs the answer before deciding whether a
    second, non-greedy attempt is worth making -- see its docstring.
    """
    return any(kana not in window for kana in _FOREIGN_SCRIPT_RUN.findall(resolved))


def _grounded(resolved: str, window: str) -> bool:
    """Every number, named person, and script ``resolved`` uses also appears
    somewhere in ``window``.

    ``window`` is the target and its context joined, so anything the target
    utterance itself already said is never flagged -- only something the
    resolver introduced that the window never mentioned. Not every kind of
    invention: a resolver could still substitute one name in the window for
    another, correctly-spelled one, and neither this nor #366's review found a
    check for that which does not need a second model.
    """
    return (
        all(digits in window for digits in _DIGIT_RUN.findall(resolved))
        and all(name in window for name in _NAMED_PERSON.findall(resolved))
        and not _leaks_foreign_script(resolved, window)
    )


_TRAILING_PUNCTUATION = re.compile(r"[.!?…\s]+$")
_TARGET_ENDING_LENGTH = 4
"""How many trailing characters of the target must survive into the resolved
sentence. Unmeasured -- like RETRY_BACKOFF_SEC, nobody has run this against a
labelled set -- picked short on purpose: a target's own reference ("그거") can
sit right next to its verb ending in a short utterance ("그거 할게요"), and a
window wide enough to require the pronoun itself to survive would reject the
very rewrite this check exists to allow. Four characters is enough to catch
"드릴게요" / "하겠습니다" without reaching back into the reference before it."""


_RETRY_SAMPLE_TEMPERATURE = 0.7
"""Temperature for the one resample attempt after a foreign-script leak.

Greedy decoding (``do_sample=False``, the normal path) is deterministic --
re-running the exact same prompt reproduces the exact same leak token for
token, so a retry only has a chance of landing somewhere else once decoding
stops being greedy. Unmeasured, like ``_TARGET_ENDING_LENGTH``: picked as
"enough randomness to plausibly choose a different token where the leak
happened, not so much that a second attempt is a different resolution
altogether."""


def _is_truncated(generated_length: int, max_new_tokens: int) -> bool:
    """Whether a generation used its entire token budget rather than the model
    choosing to stop.

    A pure function of the two lengths so the decision is testable without a
    model: ``generate()`` only reports the sequence it produced, and ``>=``
    (not ``==``) covers a caller that ever asks for one more token than the
    budget by mistake.
    """
    return generated_length >= max_new_tokens


def _retains_target_ending(resolved: str, target: str) -> bool:
    """Whether ``resolved`` keeps the target's own closing words, not just its
    context's.

    A real resolution rewrites the *reference* inside the target -- "그거"
    becomes "회의실 예약" -- and leaves the target's own predicate alone,
    because nothing about naming a pronoun changes what the speaker said they
    would do. Live-tested against #366's own comparison on a real transcript:
    two of nine resolutions were not a rewrite of the target at all, but a
    nearby context line the model returned instead -- neither shared the
    target's ending, and both passed ``_grounded`` anyway, because borrowing
    the *window's own* words is never "a new fact". This is the check that
    would have caught them.

    Skipped for a target too short to have ``_TARGET_ENDING_LENGTH`` characters
    after its own trailing punctuation -- there is nothing reliable to compare.
    """
    stripped = _TRAILING_PUNCTUATION.sub("", target)
    if len(stripped) < _TARGET_ENDING_LENGTH:
        return True
    return stripped[-_TARGET_ENDING_LENGTH:] in resolved


def _window_lines(request: ResolutionRequest) -> list[str]:
    return [*request.context, request.target, *request.context_after]


def _window_text(request: ResolutionRequest) -> str:
    return "\n".join(_window_lines(request))


def _semantically_grounded(
    resolved: str, window_lines: list[str], embedder: Embedder, min_similarity: float
) -> bool:
    """Whether ``resolved`` is close enough, by embedding, to at least one line
    of its own window to be the same idea rather than one the model drifted
    into while rewriting it.

    Checked independently of ``_grounded``, which only catches a number or a
    named person the window never said -- a resolver can drift in meaning
    while introducing neither. ``Embedder.embed`` promises unit-normalised
    vectors, so a dot product is already the cosine similarity; no norm to
    divide by here.

    An empty window (a target with no context on either side) has nothing to
    be close to, so nothing is asked and the sentence passes -- the digit and
    name checks are what apply to it instead.
    """
    if not window_lines:
        return True
    vectors = embedder.embed([resolved, *window_lines])
    resolved_vector, *window_vectors = vectors
    return (
        max(
            sum(a * b for a, b in zip(resolved_vector, window, strict=True))
            for window in window_vectors
        )
        >= min_similarity
    )


def _passes_grounding(
    answer: str,
    request: ResolutionRequest,
    embedder: Embedder | None,
    min_similarity: float | None,
) -> bool:
    """The digit, named-person and target-ending checks always apply; the
    embedding check only once both an embedder and a threshold are configured
    (``resolver_min_similarity`` unset skips it entirely, unchanged from
    before this existed)."""
    if not _grounded(answer, _window_text(request)):
        return False
    if not _retains_target_ending(answer, request.target):
        return False
    if embedder is None or min_similarity is None:
        return True
    return _semantically_grounded(answer, _window_lines(request), embedder, min_similarity)


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

--- 이전 발화 ---
{context}
--- 해소 대상 ---
{target}
--- 이후 발화 ---
{context_after}

"해소 대상" 문장에서 "그거", "그건", "저희 팀", "이거" 같은 대명사·생략된 지시어를 \
위 발화들(이전·이후 모두)이 실제로 가리키는 대상으로 바꿔 한 문장으로 다시 쓰세요.

규칙:
- 위 발화에 없는 새로운 사실(날짜, 숫자, 이름 등)을 만들어내지 마세요.
- 마스킹된 토큰(예: 대괄호로 묶인 표현)은 그대로 두세요.
- 위 발화들로 풀리지 않으면 "해소 대상" 문장을 그대로 반환하세요.
- "해소 대상"의 화자 시점을 그대로 유지하세요. "제가"/"저는"처럼 1인칭으로 말한 것을 \
"OOO님께서" 같은 3인칭으로 바꾸지 마세요.
- 결과 문장은 "해소 대상" 하나를 다시 쓴 것이어야 합니다. 지시어만 구체적인 대상으로 \
바꾸고, "이전 발화"나 "이후 발화"의 다른 내용을 새 문장으로 덧붙이거나 그 내용으로 \
통째로 바꾸지 마세요.
- 지시어가 가리킬 수 있는 대상이 여러 개면, "해소 대상" 바로 앞 발화에서 언급된 것을 \
우선하세요.
- 다시 쓴 문장 하나만 출력하고, 다른 설명은 붙이지 마세요.
"""


def _prompt(request: ResolutionRequest) -> str:
    context = "\n".join(request.context) if request.context else "(없음)"
    context_after = "\n".join(request.context_after) if request.context_after else "(없음)"
    return _PROMPT_TEMPLATE.format(
        context=context, target=request.target, context_after=context_after
    )


class LocalQwenResolver:
    """Weights in this process. ``Qwen/Qwen3-4B-Instruct-2507`` (Apache-2.0),
    #175's own starting candidate -- confirmed or replaced by the Korean
    judgment run the issue asks for, not assumed here.

    Loaded once per process, same as ``LocalDeberta``: a 4B-parameter model
    reloaded per call would dominate the pipeline far more than a classifier
    forward pass does.
    """

    def __init__(
        self,
        checkpoint: str,
        *,
        device: str = "cpu",
        max_new_tokens: int = 160,
        embedder: Embedder | None = None,
        min_similarity: float | None = None,
    ) -> None:
        if not checkpoint:
            raise ValueError("LocalQwenResolver needs a checkpoint")
        self._checkpoint = checkpoint
        self._device = device
        self._max_new_tokens = max_new_tokens
        self._embedder = embedder
        self._min_similarity = min_similarity
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
        log.info("extraction_resolver_loaded", checkpoint=self._checkpoint, device=self._device)

    def _generate(self, request: ResolutionRequest, *, sample: bool = False) -> str:
        """One resolution, or a ``RuntimeError`` if generation ran out of
        budget before the model chose to stop.

        A ``max_new_tokens`` cutoff mid-sentence is not a shorter answer, it is
        a broken one -- "확인은 못 했" with no verb ending left is worse than
        the raw quote it would otherwise replace, and this module's own
        docstrings elsewhere already refuse "wrong in a way the reader could
        not see" over "wrong in a way they can". Caught here rather than left
        for ``_passes_grounding`` because a cut-off sentence can still contain
        the target's own ending (#366's live comparison: one truncated answer
        opened with the target verbatim before running on) and would pass that
        check while still reading as broken.

        ``sample`` is only ever true for the one resample ``resolve`` makes
        after a foreign-script leak (see there) -- the normal call stays
        greedy and deterministic, unchanged from before this existed.
        """
        torch = self._torch
        messages = [{"role": "user", "content": _prompt(request)}]
        text = self._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        encoded = self._tokenizer(text, return_tensors="pt").to(self._device)
        generate_kwargs: dict[str, Any] = {
            "max_new_tokens": self._max_new_tokens,
            "do_sample": sample,
            "pad_token_id": self._tokenizer.eos_token_id,
        }
        if sample:
            generate_kwargs["temperature"] = _RETRY_SAMPLE_TEMPERATURE
        with torch.no_grad():
            output = self._model.generate(**encoded, **generate_kwargs)
        generated = output[0][encoded["input_ids"].shape[1] :]
        if _is_truncated(generated.shape[-1], self._max_new_tokens):
            raise RuntimeError(
                f"generation used the full {self._max_new_tokens}-token budget "
                "without the model choosing to stop -- treated as truncated"
            )
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
                if answer and _leaks_foreign_script(answer, _window_text(request)):
                    # Greedy decoding is deterministic -- retrying with the same
                    # settings would reproduce the exact same leak. This is the
                    # one case worth a second, non-greedy attempt rather than an
                    # immediate fallback: see #366's dummy-transcript finding.
                    log.info("extraction_resolver_foreign_script_retry")
                    answer = self._generate(request, sample=True)
            except Exception:  # noqa: BLE001 - #175: one bad generation must not fail the meeting
                log.warning("extraction_resolver_generation_failed", exc_info=True)
                resolved.append(request.target)
                failures += 1
                continue
            if not answer or not _passes_grounding(
                answer, request, self._embedder, self._min_similarity
            ):
                resolved.append(request.target)
                failures += 1
                continue
            resolved.append(answer)
        if failures:
            log.info("extraction_resolver_fallback", requests=len(requests), fallbacks=failures)
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

    def __init__(
        self,
        endpoint: str,
        model_version: str,
        *,
        embedder: Embedder | None = None,
        min_similarity: float | None = None,
    ) -> None:
        self._client = _resolver_client(endpoint)
        self._model_version = model_version
        self._embedder = embedder
        self._min_similarity = min_similarity

    @property
    def model_version(self) -> str:
        return self._model_version

    def _post(self, request: ResolutionRequest) -> Any:
        body = {
            "target": request.target,
            "context": list(request.context),
            "context_after": list(request.context_after),
        }
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
            if (
                not isinstance(answer, str)
                or not answer
                or not _passes_grounding(answer, request, self._embedder, self._min_similarity)
            ):
                resolved.append(request.target)
                continue
            resolved.append(answer)
        return resolved
