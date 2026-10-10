"""The reference resolver: in-process weights, our own inference server, a cloud
LLM API, a fake.

The first, second and last mirror ``pipeline.classifier``. The cloud one
(``LlmResolver``, ``resolver_impl=llm``) is the exception to that file's rule and
is opt-in the way ``classifier_impl=llm`` is: handing a commitment's context to
somebody else's model is a decision about where personal data goes (privacy.md
section 6), so it is never the default, sends masked text with the team's names
replaced, and runs under #392's rule for which meetings may go through it
(``config.llm_acknowledged_392``).

``torch`` and ``transformers`` are imported inside the class that needs them,
same reason as ``LocalDeberta``: importing at module scope would make
``apps/api`` load a generation stack to serve a health check.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from autune_core import get_logger
from autune_core.errors import PrivacyViolationError
from autune_extraction.slots import parse_due
from autune_integrations.errors import TransientIntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

from .base import Embedder, Resolution, ResolutionRequest
from .classifier import RETRY_BACKOFF_SEC
from .llm import (
    GeminiClient,
    _answer_text,
    restore_names_mapped,
    substitute_names_mapped,
    unquoted,
)

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


def _numbers(text: str) -> set[str]:
    """The numbers ``text`` says, each a whole run of digits without the zeros
    in front: "20일" says 20 and does not say 2, and "09시" says 9.

    One reading of "a number the source said" for both places a model's
    sentence is checked against what was spoken: ``_grounded`` here and the
    meeting summary's ``_kept`` (#1079, where the rule was first written)."""
    return {run.lstrip("0") or "0" for run in _DIGIT_RUN.findall(text)}


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

    A number is compared whole (``_numbers``; review of #1079). Looked for as
    text it was found inside a longer one: a window that said "20일" grounded
    "2일", and one that said "2026" grounded any of 2, 20, 26, 202 and 026 --
    a date or a count nobody said, on the card as if the speaker had.
    """
    return (
        _numbers(resolved) <= _numbers(window)
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
    return [
        *request.context,
        request.target,
        *request.context_after,
        *(text for _, text in request.related),
    ]


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
    *,
    keep_ending: bool = True,
) -> bool:
    """The digit, named-person and target-ending checks always apply; the
    embedding check only once both an embedder and a threshold are configured
    (``resolver_min_similarity`` unset skips it entirely, unchanged from
    before this existed)."""
    if not _grounded(answer, _window_text(request)):
        return False
    if keep_ending and not _retains_target_ending(answer, request.target):
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
            except PrivacyViolationError:
                # errors.py: never caught and downgraded. `check_outbound`
                # raises it inside `_post` when unmasked PII already reached
                # the DB (mkkim68's review of #366) -- a warning would bury it.
                raise
            except Exception:  # noqa: BLE001 - #175: one bad call must not fail the meeting
                # Not narrowed to `IntegrationError` (lsh2217's review of #366):
                # `HttpClient.request` wraps only timeouts, transport errors and
                # 4xx/5xx. A 200 whose body is an HTML gateway page or has a
                # broken content-encoding surfaces as `JSONDecodeError` /
                # `httpx.DecodingError`, and nothing above `tasks.py` catches it,
                # so one odd answer would fail the meeting's whole extraction.
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


_PLACEHOLDER = re.compile(r"\[사람\d+\]")


def _scrubbed(
    request: ResolutionRequest, roster: Sequence[str]
) -> tuple[ResolutionRequest, dict[str, str]]:
    """The request with the team's names replaced, and what each placeholder stood for.

    The three parts go through one substitution, so a person is the same number in
    the target and in the lines around it -- "[사람1]" in the context and in the
    target is one person, which is what lets the model resolve "그분" to them.
    """
    (scrubbed,), surface = _scrubbed_all([request], roster)
    return scrubbed, surface


def _texts(request: ResolutionRequest) -> list[str]:
    return [
        *request.context,
        request.target,
        *request.context_after,
        *(text for _, text in request.related),
    ]


def _scrubbed_all(
    requests: Sequence[ResolutionRequest], roster: Sequence[str]
) -> tuple[list[ResolutionRequest], dict[str, str]]:
    """``_scrubbed`` over several requests sent in one prompt, through one
    substitution: a person is the same number in every item, so the numbers in
    one prompt never name two people."""
    texts = [text for request in requests for text in _texts(request)]
    replaced, surface = substitute_names_mapped(texts, roster)
    out: list[ResolutionRequest] = []
    at = 0
    for request in requests:
        before = at + len(request.context)
        after = before + 1 + len(request.context_after)
        end = after + len(request.related)
        out.append(
            replace(
                request,
                target=replaced[before],
                context=tuple(replaced[at:before]),
                context_after=tuple(replaced[before + 1 : after]),
                related=tuple(
                    (line_id, text)
                    for (line_id, _), text in zip(request.related, replaced[after:end], strict=True)
                ),
            )
        )
        at = end
    return out, surface


def _own_surface(sent: ResolutionRequest, surface: dict[str, str]) -> dict[str, str]:
    """The placeholders that occur in ``sent`` and what they stood for. An answer
    in a batch is restored with these only: a placeholder from another item is a
    person this item never mentioned, which ``_restored`` refuses as invented."""
    own = {marked for text in _texts(sent) for marked in _PLACEHOLDER.findall(text)}
    return {marked: name for marked, name in surface.items() if marked in own}


def _restored(answer: str, surface: dict[str, str]) -> str | None:
    """The answer with each placeholder put back as the name it stood for.

    The sentence is stored as an item's description and read by the team, so a
    "[사람1]" left in it would be nonsense -- and the reverse of the classifier,
    whose labels carry no name to restore. ``None`` when the model wrote a
    placeholder that was never sent: it invented a person.
    B's one restore (``llm.restore_names_mapped``, also ``tools.restore_names``).
    """
    return restore_names_mapped(answer, surface)


def _ends_the_same(answer: str, target: str) -> bool:
    """The answer is the target with, at most, its closing punctuation changed."""
    return answer.strip().rstrip(" .!?…") == target.strip().rstrip(" .!?…")


_SENTENCE_BREAK = re.compile(r"(?:습니다|어요|아요|여요|이요|이에요|예요|죠|네요|군요)\s+\S")
"""A finished sentence with more text after it. A rewrite that contains one the
target does not is two sentences pasted into one -- "패키지 사고 우편함에 안 들어가는
건이요 서버 쪽은 고쳤는데 ..." -- which is what a model does when it merges lines of
two different subjects."""

_BRACKETED = re.compile(r"\[[^\]]*\]|\([^)]*\)")
MAX_GROWTH = 3
"""A sentence more than this many times as long as the one it rewrites (or 80
characters, whichever is more) is a paragraph, not a resolved reference."""


_WORD = re.compile(r"[가-힣A-Za-z0-9]+")
_PARTICLE = r"(?:은|는|이|가|을|를|도|만|에|의|로|에는|에도|까지|부터)?"
_STANDS_FOR = re.compile(
    rf"^(?:[그이저](?:거|건|걸|게|것){_PARTICLE}|그(?:날|때|쪽){_PARTICLE}|[그이])$"
)
"""A word of the said line a rewrite may leave out: the one that stood for
something -- "그건", "그것도", "그날은", the "그" of "그 부분은" -- which is the word
it was asked to replace. Spelled out, not "anything starting with 그/이/저":
"저는", "이번" and "그럼" start so too, and a rewrite that dropped one of those
changed who promised or when."""


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _stems(text: str) -> set[str]:
    """As ``related._stems``: a word without its particle or ending."""
    return {word[:2] for word in _WORD.findall(text.lower()) if len(word) >= 2}


def _keeps_what_was_said(answer: str, target: str) -> bool:
    """Every word of the said line is still in the rewrite, the pointing words
    aside (``_STANDS_FOR``).

    A commitment's rewrite is the said line with what it pointed at filled in;
    the instructions say so and ``_retains_target_ending`` holds the last few
    letters to it. The words before the ending were held to nothing, and on 20
    invented commitments (2026-10-08) two answers came back with one changed
    into a word nobody said -- "아," into "아프,", "감사합니다." into "감사해," --
    and the rest of the sentence right, so every other check passed. A changed
    word is not a filled reference, and the said line is the better row.
    """
    said = _words(answer)
    return all(word in said or _STANDS_FOR.match(word) for word in _words(target))


def _filled_from_what_followed(answer: str, request: ResolutionRequest) -> bool:
    """Whether the rewrite added a word that only a line said *after* the
    target holds.

    The lines after a commitment are offered because an answer to it can name
    what it was about. They are also where the meeting moves on: of 20 invented
    commitments (2026-10-08), "문구는 제가 정리해서 공유드릴게요" -- about the
    mail just decided -- came back as the wording of the refund notice, which
    the next speaker had brought up. Nothing in the answer was a number or a
    name, and its ending was the target's, so it passed. What a speaker points
    at was said before they spoke; a word found in a following line and in no
    earlier or related one is the next subject, not this one's.
    """
    added = _stems(answer) - _stems(request.target)
    earlier = _stems(" ".join([*request.context, *(text for _, text in request.related)]))
    later = _stems(" ".join(request.context_after))
    return bool((added & later) - earlier)


def _sound(answer: str, request: ResolutionRequest) -> bool:
    """What a resolved sentence must not do that the groundedness checks miss.

    Measured on 24 dummy commitments (2026-09-30), the cheap model answered every
    one and still: added a bracketed or parenthesised clause of its own, dropped
    the words a deadline was read from ("매주 월요일에" became "주 단위로"), and now
    and then ran on to three times the length. None of those is a number or a
    name, so ``_grounded`` lets them through.

    - No bracketed or parenthesised span that the target and its context do not
      already contain (a masked token is one of those).
    - The deadline phrase the target carries, if any, is still there.
    - No longer than ``MAX_GROWTH`` times the target, with 80 characters as the
      floor.
    - No finished sentence followed by more text that the target does not already
      have (``_SENTENCE_BREAK``): two lines merged into one is not a resolved
      reference.
    """
    window = " ".join(_window_lines(request))
    if any(span not in window for span in _BRACKETED.findall(answer)):
        return False
    due = parse_due(request.target, None)
    if due is not None and due.text and due.text not in answer:
        return False
    if len(_SENTENCE_BREAK.findall(answer)) > len(_SENTENCE_BREAK.findall(request.target)):
        return False
    return len(answer) <= max(80, MAX_GROWTH * len(request.target))


_SUMMARY_INTRO = """\
다음은 회의 발화 목록입니다. 앞의 번호는 이 목록 안에서만 쓰는 번호이고, [대상]이 정리할 \
문장입니다. [앞]과 [뒤]는 대상 바로 앞뒤의 발화, [관련]은 회의의 다른 곳에서 비슷한 \
말을 한 발화입니다 (관련 없는 것도 섞여 있을 수 있습니다).

{lines}

"""
_SUMMARY_TASK = """\
[대상] 문장에서 "그거", "그건", "이거", "저희 팀", "표", "이번 빌드" 같은 대명사나 빠져 있는 \
대상을, 위 발화들([앞], [뒤], [관련] 모두)이 실제로 가리키는 것으로 채워 한 문장으로 다시 \
쓰세요.

규칙:
- 위 발화에 없는 새로운 사실(날짜, 숫자, 이름 등)을 만들어내지 마세요.
- 마스킹된 토큰(예: 대괄호로 묶인 표현)은 그대로 두세요.
- [대상]의 화자 시점과 문장 끝 어미("~할게요", "~하겠습니다", "~보려고요")를 그대로 유지하세요. \
"제가"/"저는"을 3인칭으로 바꾸지 마세요.
- [대상]에 있는 날짜·기한·수량 표현은 그대로 두세요.
- 지시어나 빠진 대상만 구체적인 말로 채우세요. 채우는 말은 짧은 명사구 하나로 하고, 다른 \
발화의 문장을 통째로 옮겨 오지 마세요. 괄호나 대괄호는 새로 쓰지 마세요.
- 서로 다른 주제의 발화를 섞지 마세요. 여러 주제가 보이면 [대상] 바로 앞 발화의 주제 하나만 \
쓰고, 나머지는 무시하세요.
- 이미 분명한 문장은 그대로 "summary"에 쓰세요.
- 채우는 데 실제로 쓴 발화의 번호를 "used"에 적으세요. [대상] 자신의 번호는 적지 \
마세요. 발화들로 풀리지 않으면 [대상] 문장을 그대로 "summary"에 쓰고 "used"는 빈 목록으로 \
두세요.

예시 1
1 [앞] 지난주에 만든 온보딩 문서가 아직 초안 상태예요
2 [대상] 그건 제가 금요일까지 마무리할게요
{{"summary": "온보딩 문서는 제가 금요일까지 마무리할게요", "used": [1]}}

예시 2 (이미 분명한 문장)
1 [앞] 회의 끝나고 점심 먹으러 가요
2 [대상] 제가 내일까지 견적서를 보낼게요
{{"summary": "제가 내일까지 견적서를 보낼게요", "used": []}}

예시 3 (주제가 섞여 있을 때는 바로 앞 주제 하나만)
1 [관련] 주차 공간이 부족하다는 얘기가 있었어요
2 [앞] 로그인 오류는 서버 쪽에서 고쳤어요
3 [대상] 그건 제가 이번 배포에 넣을게요
{{"summary": "로그인 오류 수정은 제가 이번 배포에 넣을게요", "used": [2]}}

"""
_SUMMARY_OUTPUT = """\
JSON 하나만 출력하세요: {{"summary": "다시 쓴 한 문장", "used": [번호, ...]}}
"""
_SUMMARY_PROMPT = _SUMMARY_INTRO + _SUMMARY_TASK + _SUMMARY_OUTPUT
_DECISION_INTRO = """\
다음은 회의 발화 목록입니다. 앞의 번호는 이 목록 안에서만 쓰는 번호이고, [대상]은 회의에서 \
무언가를 하기로 정한 말입니다. [앞]과 [뒤]는 대상 바로 앞뒤의 발화, [관련]은 회의의 다른 \
곳에서 비슷한 말을 한 발화입니다 (관련 없는 것도 섞여 있을 수 있습니다).

{lines}

"""
_DECISION_TASK = """\
이 회의에서 무엇이 결정되었는지를 한 문장으로 쓰세요. [대상]이 "그렇게 하죠", "그 방향으로 \
가요"처럼 가리키기만 하면, 위 발화들에서 가리키는 것을 찾아 구체적으로 쓰세요.

규칙:
- 위 발화에 없는 새로운 사실(날짜, 숫자, 이름 등)을 만들어내지 마세요.
- 마스킹된 토큰(예: 대괄호로 묶인 표현)은 그대로 두세요.
- 결정된 내용만 쓰세요. 누가 말했는지, 누가 동의했는지는 쓰지 마세요. "~하기로 했습니다" \
또는 "~로 정했습니다"로 끝내세요.
- [대상]에 있는 날짜·기한·수량 표현은 그대로 두세요.
- 괄호나 대괄호는 새로 쓰지 마세요. 결정은 하나만 쓰고, 서로 다른 주제의 발화를 한 문장에 \
섞지 마세요.
- 결정된 내용을 고른 발화의 번호를 "used"에 적으세요. [대상] 자신의 번호는 적지 마세요. \
발화들로 더 구체적으로 쓸 수 없으면 [대상]의 뜻을 그대로 "~하기로 했습니다" 형태로 쓰고 \
"used"는 빈 목록으로 두세요.

예시 1
1 [앞] 검색 결과를 인기순으로 할지 최신순으로 할지 고민이에요
2 [앞] 인기순이 클릭률이 더 높아요
3 [대상] 그럼 그렇게 하죠
{{"summary": "검색 결과 정렬은 인기순으로 하기로 했습니다", "used": [1, 2]}}

예시 2 (이미 구체적인 대상)
1 [대상] 배포는 다음 주 화요일로 미루는 걸로 합시다
{{"summary": "배포는 다음 주 화요일로 미루기로 했습니다", "used": []}}

예시 3 (주제가 섞여 있을 때는 대상이 가리키는 하나만)
1 [앞] 예산은 이번 분기 동결이에요
2 [앞] 회의실은 다음 달부터 예약제로 해요
3 [대상] 네 그걸로 가죠
{{"summary": "회의실은 다음 달부터 예약제로 운영하기로 했습니다", "used": [2]}}

"""
_DECISION_OUTPUT = """\
JSON 하나만 출력하세요: {{"summary": "결정된 내용 한 문장", "used": [번호, ...]}}
"""
_DECISION_PROMPT = _DECISION_INTRO + _DECISION_TASK + _DECISION_OUTPUT
_BATCH_SCOPE = """\
항목마다 번호가 1부터 다시 시작하고, 그 번호는 그 항목 안에서만 씁니다. [앞]과 [뒤]는 대상 \
바로 앞뒤의 발화, [관련]은 회의의 다른 곳에서 비슷한 말을 한 발화입니다 (관련 없는 것도 섞여 \
있을 수 있습니다). 항목은 서로 따로입니다. 한 항목을 쓸 때는 그 항목의 발화만 보세요.

{items}

아래 규칙과 예시는 항목 하나를 쓰는 방법입니다. 항목마다 따로 적용하세요.

"""
_BATCH_INTRO = {
    "commitment": "다음은 회의 발화를 항목별로 나눈 목록입니다. "
    "항목마다 [대상]이 정리할 문장입니다. " + _BATCH_SCOPE,
    "decision": "다음은 회의 발화를 항목별로 나눈 목록입니다. "
    "항목마다 [대상]은 회의에서 무언가를 하기로 정한 말입니다. " + _BATCH_SCOPE,
}
_BATCH_OUTPUT = """\
항목마다 예시와 같은 답을 쓰고 "item"에 항목 번호를 적으세요. 모든 항목에 답하세요. JSON 하나만 \
출력하세요: {{"items": [{{"item": 1, "summary": "...", "used": [번호, ...]}}, ...]}}
"""
"""The batch form of the two prompts (``LlmResolver._resolve_batch``): the same
rules and worked examples, word for word, between an introduction that says the
lines come in separately numbered items and an answer that is one list. Only the
framing is new -- what a good answer for one item looks like is what was measured
on the single prompt (2026-09-30)."""
MAX_USED = 4
_LABELS = ("앞", "대상", "뒤", "관련")


def _numbered(request: ResolutionRequest) -> list[tuple[str, str, str]]:
    """The window and the candidates as ``(utterance id, label, text)``, in the order
    the prompt numbers them. An id is empty for a line the caller gave no id for --
    such a line can be read but never cited."""

    def ids(given: tuple[str, ...], n: int) -> list[str]:
        return list(given) if len(given) == n else [""] * n

    before = ids(request.context_ids, len(request.context))
    after = ids(request.context_after_ids, len(request.context_after))
    lines = [(i, _LABELS[0], t) for i, t in zip(before, request.context, strict=True)]
    lines.append((request.target_id, _LABELS[1], request.target))
    lines += [(i, _LABELS[2], t) for i, t in zip(after, request.context_after, strict=True)]
    lines += [(i, _LABELS[3], t) for i, t in request.related]
    return lines


def _summary_prompt(numbered: list[tuple[str, str, str]], purpose: str = "commitment") -> str:
    lines = "\n".join(f"{n} [{label}] {text}" for n, (_, label, text) in enumerate(numbered, 1))
    template = _DECISION_PROMPT if purpose == "decision" else _SUMMARY_PROMPT
    return template.format(lines=lines)


def _batch_prompt(items: list[list[tuple[str, str, str]]], purpose: str = "commitment") -> str:
    """Several requests' numbered lines in one prompt, each item numbered from 1."""
    blocks = [
        f"항목 {k}\n"
        + "\n".join(f"{n} [{label}] {text}" for n, (_, label, text) in enumerate(lines, 1))
        for k, lines in enumerate(items, 1)
    ]
    decision = purpose == "decision"
    intro = _BATCH_INTRO["decision" if decision else "commitment"]
    task = _DECISION_TASK if decision else _SUMMARY_TASK
    return (intro + task + _BATCH_OUTPUT).format(items="\n\n".join(blocks))


_CITED_NUMBER = re.compile(r"\D*(\d+)\D*")
"""A citation written as a string: "1", "발화 1", "1번"."""


def _line_number(cited: Any) -> int | None:
    """One entry of "used" as a line number, however the model wrote it.

    An int, an integral float (``1.0``), or a string holding one number ("1",
    "발화 1") -- the shapes C met from the same models (#503, #523). A bool, a
    fraction, or a string with no number or two is not a citation.
    """
    if isinstance(cited, bool):
        return None
    if isinstance(cited, int):
        return cited
    if isinstance(cited, float):
        return int(cited) if cited.is_integer() else None
    if isinstance(cited, str):
        match = _CITED_NUMBER.fullmatch(cited)
        return int(match.group(1)) if match else None
    return None


def _read_summary(answer: str, numbered: list[tuple[str, str, str]]) -> tuple[str, tuple[str, ...]]:
    """The model's summary and the ids of the lines it says it used.

    Only numbers of lines that exist and carry an id count, never the target's
    own. At most ``MAX_USED`` are kept, **the first the model listed** -- the
    ones it leaned on, not the earliest said -- then put in spoken order for
    the reader. Anything unreadable is an empty summary, which the caller
    treats as no answer.

    A summary that cites nothing is still accepted when it passes the checks:
    what it added must already be in the window (``_passes_grounding``), and
    the original sits beneath it on the screen.
    """
    match = re.search(r"\{.*\}", answer, re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        return "", ()
    cited = data.get("used")
    used: list[str] = []
    for entry in cited if isinstance(cited, list) else []:
        number = _line_number(entry)
        if number is None or not 1 <= number <= len(numbered):
            continue
        line_id, label, _ = numbered[number - 1]
        if line_id and label != _LABELS[1] and line_id not in used:
            used.append(line_id)
    order = {line_id: n for n, (line_id, _, _) in enumerate(numbered)}
    return data["summary"], tuple(sorted(used[:MAX_USED], key=lambda i: order[i]))


_PROMPT_BUDGET = MAX_OUTBOUND_CHARS - 200
"""What the prompt may take of the outbound limit. ``check_outbound`` counts
every string in the body, and the rest ("user", the MIME type) is a few dozen
characters; the margin covers them."""


def _fitted(
    request: ResolutionRequest, render: Any, budget: int = _PROMPT_BUDGET
) -> ResolutionRequest | None:
    """The request cut down until ``render(request)`` fits ``budget``, or
    ``None`` when even the target alone does not (#530 review).

    Over the limit, ``check_outbound`` raises ``PrivacyViolationError`` -- on
    purpose, and never caught -- and the meeting's extraction fails. So the
    request shrinks before it is sent: the least alike candidate first
    (``related`` is best first), then the context line farthest from the
    target, the one after it on a tie (what a pronoun points at was usually
    said before). A target too long on its own is not sent: the item keeps
    its raw quote. Nothing is truncated mid-line.
    """
    fitted = request
    while len(render(fitted)) > budget:
        if fitted.related:
            fitted = replace(fitted, related=fitted.related[:-1])
            continue
        before, after = len(fitted.context), len(fitted.context_after)
        if before == 0 and after == 0:
            return None
        if before > after:
            ids = fitted.context_ids[1:] if len(fitted.context_ids) == before else ()
            fitted = replace(fitted, context=fitted.context[1:], context_ids=ids)
        else:
            keep = after - 1
            ids = fitted.context_after_ids[:keep] if len(fitted.context_after_ids) == after else ()
            fitted = replace(
                fitted, context_after=fitted.context_after[:keep], context_after_ids=ids
            )
    return fitted


def _cut_like(request: ResolutionRequest, fitted: ResolutionRequest) -> ResolutionRequest:
    """``request`` cut to the lines ``fitted`` kept -- the same cut on the text
    before names were replaced, so the answer is checked against what was
    sent and not against lines the model never saw."""
    before, after = len(fitted.context), len(fitted.context_after)
    return replace(
        request,
        context=request.context[len(request.context) - before :] if before else (),
        context_ids=fitted.context_ids,
        context_after=request.context_after[:after],
        context_after_ids=fitted.context_after_ids,
        related=request.related[: len(fitted.related)],
    )


MAX_BATCH = 6
"""Requests per call at most when they carry ids (``LlmResolver._batches``).
One call per item spent a free tier on one long meeting -- the second model
allows twenty calls a day -- and batching is what keeps a meeting inside it.
The cap bounds what one unreadable answer costs: every item in that call loses
its first try. The outbound limit usually stops a batch sooner, since the
shared rules and examples take a third of it."""

_Item = tuple[int, ResolutionRequest]
"""A request and its position in the meeting's list."""
_Sent = tuple[int, ResolutionRequest, dict[str, str], list[tuple[str, str, str]]]
"""An item as sent in a batch: position, request, its own placeholders, its lines."""
_Prepared = tuple[
    dict[str, Any], ResolutionRequest, dict[str, str], list[tuple[str, str, str]] | None
]
"""A single request as sent: body, request cut to it, placeholders, lines."""

MAX_ESCALATIONS = 5
"""Second-model calls per meeting at most. The second model's free tier allows
twenty a day (#530 review); one meeting of unsound first answers must not spend
all of them. Past this, an unsound first answer is the raw quote."""


class LlmResolver(GeminiClient):
    """The same rewrite as ``LocalQwenResolver``, by a cloud LLM API (#175).

    Exists because the local model needs a GPU and ``hosted`` needs a server of
    ours, and the mentoring of 2026-09-23 says to use an LLM wherever a trained
    model costs more than it earns. Opt-in and never the default
    (``resolver_impl=llm``): it sends the commitment and up to four lines before
    and two after it out of our infrastructure.

    **What leaves, and what stops it** -- the same as ``LlmClassifier``: masked
    utterance text only, no speaker, no id, no meeting; only consenting speakers'
    lines (``resolve_commitment_references`` filters before this is called); the
    team's names replaced by ``[사람N]`` and restored in the answer, so a name
    never reaches the provider and never stays a placeholder in a description;
    every request through ``HttpClient``, whose outbound check refuses an
    unmasked number, address or account. A free-tier key may let the provider
    keep what it is sent, so it is for dummy meetings only (#392).

    **Several items per call.** A meeting's id-carrying requests -- every item
    and decision summary -- share calls, up to ``MAX_BATCH`` each and within the
    outbound limit, instead of one call apiece: a long meeting no longer spends
    a free tier's daily allowance on itself. Each item keeps its own numbered
    lines, the names are numbered once across the call, and each answer passes
    the same checks a single one does (``_resolve_batch``). Nothing more leaves
    than before -- the same lines, in fewer requests.

    **One bad answer degrades to the raw quote**, exactly as the other
    resolvers: a failed call, a blank or multi-line answer, an invented
    placeholder, or a sentence that fails the groundedness checks all return the
    target unchanged. A privacy refusal is the exception -- it is raised, never
    downgraded.
    """

    step = "resolver"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_sec: float = 60.0,
        fallback_model: str = "",
        embedder: Embedder | None = None,
        min_similarity: float | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_sec=timeout_sec,
            fallback_model=fallback_model,
        )
        self._embedder = embedder
        self._min_similarity = min_similarity

    def _accept(
        self,
        answer: str,
        request: ResolutionRequest,
        surface: dict[str, str],
        numbered: list[tuple[str, str, str]] | None,
    ) -> Resolution | None:
        """The answer as the sentence to store and the lines it cites, or ``None``
        when it should not be used.

        Checks that need no model, cheapest first: one line, no placeholder the
        model invented, then ``_sound`` (what the sentence adds and drops) and the
        groundedness checks every resolver passes. A sentence that came back
        unchanged cites nothing -- there is nothing it was written from.
        """
        used: tuple[str, ...] = ()
        if numbered is None:
            text = answer
        else:
            text, used = _read_summary(answer, numbered)
        text = unquoted(text)
        if not text or "\n" in text:
            return None
        restored = _restored(text, surface)
        if restored is None or not _sound(restored, request):
            return None
        decision = request.purpose == "decision"
        if not _passes_grounding(
            restored, request, self._embedder, self._min_similarity, keep_ending=not decision
        ):
            return None
        # A decision's write-up is a new sentence about several turns, the ones
        # after its substance included; these two hold a commitment's rewrite to
        # the line it rewrites.
        if not decision and not (
            _keeps_what_was_said(restored, request.target)
            and not _filled_from_what_followed(restored, request)
        ):
            return None
        if _ends_the_same(restored, request.target):
            # Only the full stop differs: nothing was resolved, so nothing was used.
            return Resolution(request.target)
        return Resolution(restored, used)

    def _ask(self, model: str | None, body: dict[str, Any], index: int) -> str | None:
        """One model's answer text; ``None`` if the call failed. A privacy refusal is
        raised, never downgraded (errors.py)."""
        try:
            if model is None:
                return _answer_text(self._post(body, index=index))
            return _answer_text(self._post_to(model, body, index=index))
        except PrivacyViolationError:
            raise
        except Exception as exc:  # noqa: BLE001 - #175: one bad call must not fail the meeting
            # The class name only: a traceback can carry the request's text.
            log.warning("extraction_resolver_call_failed", error=type(exc).__name__)
            return None

    def _single(self, request: ResolutionRequest) -> _Prepared:
        """One request as the body to send, the request cut to what is sent, the
        placeholders' names and the numbered lines. ``ValueError`` when even the
        target alone does not fit (``_fitted``)."""
        scrubbed, surface = _scrubbed(request, self._roster)

        def render(r: ResolutionRequest) -> str:
            return _summary_prompt(_numbered(r), r.purpose) if r.target_id else _prompt(r)

        sent = _fitted(scrubbed, render)
        if sent is None:
            raise ValueError("target too long")
        request = _cut_like(request, sent)
        numbered = _numbered(sent) if request.target_id else None
        generation: dict[str, Any] = {"temperature": 0}
        if numbered is not None:
            generation["responseMimeType"] = "application/json"
        body = {
            "contents": [{"role": "user", "parts": [{"text": render(sent)}]}],
            "generationConfig": generation,
        }
        return body, request, surface, numbered

    def _may_escalate(self, escalations: list[int] | None) -> bool:
        """Whether the second model may be asked now; counts the call if so."""
        if not self._fallback or self.last_model == self._fallback:
            return False
        if escalations is not None:
            if escalations[0] >= MAX_ESCALATIONS:
                return False
            escalations[0] += 1
        log.info("extraction_resolver_escalated", model=self._model, second=self._fallback)
        return True

    def _resolve_one(
        self, request: ResolutionRequest, index: int, escalations: list[int] | None = None
    ) -> Resolution:
        """The cheap model first; the second model once if its answer is not sound.

        The first model (``model``) answers everything. When its answer fails a
        check -- it added a bracketed clause, dropped the deadline, ran on -- the
        second model gets the same request once, and when that fails too the raw
        quote stands. A busy first model already falls through to the second
        (``GeminiClient._post``), so a second model that has answered is not asked
        again for the same request.

        **Two modes.** A request that carries ids (``target_id``) is asked to
        summarise from the numbered window and candidates and to say which lines
        it used, and answers as JSON. One without is the plain rewrite it always
        was.
        """
        raw = Resolution(request.target)
        try:
            body, request, surface, numbered = self._single(request)
        except ValueError:
            # Ids only: the line is meeting content.
            log.info("extraction_resolver_target_too_long", target_id=request.target_id)
            return raw
        first = self._ask(None, body, index)
        if first is None:
            return raw
        accepted = self._accept(first, request, surface, numbered)
        if accepted is not None:
            return accepted
        if not self._may_escalate(escalations):
            return raw
        second = self._ask(self._fallback, body, index)
        accepted = self._accept(second, request, surface, numbered) if second is not None else None
        return accepted if accepted is not None else raw

    def _batch(self, items: list[_Item]) -> tuple[dict[str, Any], list[_Sent]]:
        """Several id-carrying requests of one purpose as one body, each sent
        whole: ``_batches`` packs only what fits uncut."""
        scrubbed, surface = _scrubbed_all([request for _, request in items], self._roster)
        numbered = [_numbered(one) for one in scrubbed]
        prompt = _batch_prompt(numbered, items[0][1].purpose)
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        sent = [
            (index, request, _own_surface(one, surface), lines)
            for (index, request), one, lines in zip(items, scrubbed, numbered, strict=True)
        ]
        return body, sent

    def _fits(self, items: list[_Item]) -> bool:
        """``items`` as one prompt stays inside the outbound limit -- measured on
        the prompt as it would be sent, names replaced, never estimated: over the
        limit ``check_outbound`` fails the meeting."""
        body, _ = self._batch(items)
        return len(body["contents"][0]["parts"][0]["text"]) <= _PROMPT_BUDGET

    def _batches(self, items: list[_Item]) -> list[list[_Item]]:
        """``items`` packed in order into prompts of at most ``MAX_BATCH`` that fit.
        An item that fits with no other is a batch of one."""
        out: list[list[_Item]] = []
        current: list[_Item] = []
        for item in items:
            trial = [*current, item]
            if current and (len(trial) > MAX_BATCH or not self._fits(trial)):
                out.append(current)
                trial = [item]
            current = trial
        if current:
            out.append(current)
        return out

    def _read_batch(self, answer: str, sent: list[_Sent]) -> dict[int, Resolution]:
        """Each item's answer through ``_accept``, keyed by request index. An item
        the model skipped, answered unsoundly, or numbered a second time (the
        first answer counts) has no entry."""
        match = re.search(r"\{.*\}", answer, re.S)
        try:
            data = json.loads(match.group(0)) if match else {}
        except ValueError:
            data = {}
        entries = data.get("items") if isinstance(data, dict) else None
        out: dict[int, Resolution] = {}
        seen: set[int] = set()
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            k = _line_number(entry.get("item"))
            if k is None or not 1 <= k <= len(sent) or k in seen:
                continue
            seen.add(k)
            index, request, surface, numbered = sent[k - 1]
            one = json.dumps(
                {"summary": entry.get("summary"), "used": entry.get("used")}, ensure_ascii=False
            )
            accepted = self._accept(one, request, surface, numbered)
            if accepted is not None:
                out[index] = accepted
        return out

    def _resolve_batch(self, items: list[_Item], escalations: list[int]) -> dict[int, Resolution]:
        """Several requests in one call; whatever it leaves unresolved goes to the
        second model in one more call -- on the single prompt when that is one
        request.

        A call that fails costs its items their rewrite, as a failed call costs
        one item its rewrite in ``_resolve_one``: ``_post`` has already retried and
        fallen back by then, so asking again item by item would only spend the
        quota on a provider that is not answering."""
        body, sent = self._batch(items)
        first = self._ask(None, body, items[0][0])
        done = self._read_batch(first, sent) if first is not None else {}
        left = [(index, request) for index, request in items if index not in done]
        log.info("extraction_resolver_batch", items=len(items), resolved=len(done))
        if first is not None and left and self._may_escalate(escalations):
            if len(left) == 1:
                index, request = left[0]
                try:
                    single, cut, surface, numbered = self._single(request)
                except ValueError:  # it fitted a longer prompt; kept so it can never fail
                    single = {}
                second = self._ask(self._fallback, single, index) if single else None
                accepted = self._accept(second, cut, surface, numbered) if second else None
                if accepted is not None:
                    done[index] = accepted
            else:
                body, sent = self._batch(left)
                second = self._ask(self._fallback, body, left[0][0])
                if second is not None:
                    done.update(self._read_batch(second, sent))
        return {index: done.get(index, Resolution(request.target)) for index, request in items}

    def resolve_with_evidence(self, requests: list[ResolutionRequest]) -> list[Resolution]:
        """One meeting's requests, in as few calls as fit.

        Requests that carry ids are packed, per purpose and in order, into prompts
        of up to ``MAX_BATCH`` (``_batches``). A batch of one, and a request with no
        ids, takes the single prompt (``_resolve_one``) -- the form every check
        here was measured on. The second model is asked at most
        ``MAX_ESCALATIONS`` times across them, a call counting once whether it
        carries one request or several."""
        escalations = [0]
        out: dict[int, Resolution] = {}
        cited = [(index, request) for index, request in enumerate(requests) if request.target_id]
        for purpose in dict.fromkeys(request.purpose for _, request in cited):
            same = [(index, request) for index, request in cited if request.purpose == purpose]
            for batch in self._batches(same):
                if len(batch) > 1:
                    out.update(self._resolve_batch(batch, escalations))
        return [
            out[index] if index in out else self._resolve_one(request, index, escalations)
            for index, request in enumerate(requests)
        ]

    def resolve(self, requests: list[ResolutionRequest]) -> list[str]:
        return [resolution.text for resolution in self.resolve_with_evidence(requests)]
