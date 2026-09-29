"""Template-item verifiers: a fake for tests, and Gemini over HTTP.

A verifier is asked about ambiguous utterances only (``autune_gap.verification``)
and answers yes or no per candidate item it was shown. The provider sits behind
``base.TemplateVerifier``; replacing Gemini with another provider is a new class
here and a registry entry, and nothing outside this package changes.

**What the Gemini implementation sends** — per request, never more than
``MAX_OUTBOUND_CHARS``:

- ``INSTRUCTIONS``, fixed text.
- The candidate items offered in this request: each item's name, its question
  and up to ``AUTUNE_GAP_VERIFIER_EXAMPLES`` example sentences — template-file
  content, not meeting content.
- The ambiguous utterances, each on its own numbered line, as module A masked
  them. **Names said aloud are not masked** (module A has no pattern for them),
  so a name in one of these lines goes too. No speaker, no timestamp, no meeting
  or utterance id, no neighbouring line.

Every request goes through ``autune_integrations.HttpClient``, so
``check_outbound`` scans every string in the body and refuses an unmasked phone
number, e-mail or account number, and an oversized body.
"""

from __future__ import annotations

import json
import re
import string
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from autune_core import get_logger
from autune_core.errors import PrivacyViolationError
from autune_integrations.errors import IntegrationError, TransientIntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

if TYPE_CHECKING:
    # Types only: ``template`` imports ``pipeline.base`` (#456), so a runtime import
    # here would close a cycle through ``pipeline.registry``.
    from autune_gap.verification import Question

log = get_logger(__name__)


class FakeVerifier:
    """No network, deterministic. Answers from ``decide`` when given one, and
    otherwise confirms each question's first candidate — the embedder's own
    nearest item — which makes a run with it equal to the embedding without a
    floor, not an approximation of any LLM.

    Counts what it was asked, so a test can assert that confident and ignored
    utterances never reach a verifier."""

    model_version = "fake"

    def __init__(self, decide: Callable[[Question], frozenset[str] | None] | None = None) -> None:
        self._decide = decide
        self.asked: list[Question] = []

    def verify(self, questions: list[Question]) -> list[frozenset[str] | None]:
        self.asked.extend(questions)
        if self._decide is not None:
            return [self._decide(question) for question in questions]
        return [
            frozenset({question.candidates[0].key}) if question.candidates else frozenset()
            for question in questions
        ]


INSTRUCTIONS = (
    "회의 발화가 체크리스트 후보 항목을 실제로 논의했는지 판정하세요.\n"
    "- 각 [발화]는 따로 판단하고, 그 줄에 적힌 후보 글자만 판단하세요.\n"
    "- 발화가 그 항목의 내용을 실제로 말했을 때만 고르세요. 예: 누가 언제까지 할지를 정함, "
    "무엇이 먼저 끝나야 하는지를 말함.\n"
    "- 단어나 말투가 비슷한 것만으로는 고르지 마세요. 맞장구, 회의 마무리, 진행 멘트, "
    "방향만 말하고 항목 내용이 없는 말은 어떤 후보도 아닙니다.\n"
    "- 여러 후보를 고를 수 있고, 하나도 고르지 않을 수 있습니다.\n"
    "- 목록에 없는 글자나 새 항목은 쓰지 마세요.\n"
    'JSON 한 줄로만 답하세요: {"answers": {"발화번호": ["후보글자", ...]}}'
)
"""The whole of what the model is told. Korean, because the utterances and the
items are; kept short because ``check_outbound`` counts it against the same
4,000 characters as the utterances."""

_LETTERS = string.ascii_uppercase
_RETRY_BACKOFF_SEC = (2.0, 5.0, 10.0)
"""Module B's backoff, for the same provider and the same busy-model 503s."""
_OVERHEAD = len(INSTRUCTIONS) + 300
"""Instructions plus JSON punctuation and markers the budget leaves room for."""


def render(questions: list[Question]) -> tuple[str, dict[int, dict[str, str]]]:
    """The request text, and line number -> {letter: item key} for each line.

    Candidates are lettered once per request, so an item offered for three
    lines is described once. A line lists only its own letters, and ``parse``
    keeps only those.
    """
    letter_of: dict[str, str] = {}
    described: list[str] = []
    for question in questions:
        for candidate in question.candidates:
            if candidate.key in letter_of:
                continue
            letter = _LETTERS[len(letter_of)]
            letter_of[candidate.key] = letter
            examples = " / ".join(candidate.examples)
            described.append(
                f"{letter}. {candidate.item} -- {candidate.question}"
                + (f"\n   예: {examples}" if examples else "")
            )

    lines = ["[후보]", *described, ""]
    offered: dict[int, dict[str, str]] = {}
    for number, question in enumerate(questions, start=1):
        letters = {letter_of[candidate.key]: candidate.key for candidate in question.candidates}
        offered[number] = letters
        lines.append(f"[발화 {number}] {question.utterance}")
        lines.append(f"  후보: {', '.join(sorted(letters))}")
    return "\n".join(lines), offered


def parse(answer: str, offered: dict[int, dict[str, str]]) -> dict[int, frozenset[str]]:
    """``{"answers": {"1": ["A"]}}`` -> ``{1: {"dependency"}}``.

    A line the answer skips is an empty answer for it. A letter the line was not
    offered, a line number that was not asked, and anything that is not the
    JSON shape are dropped rather than guessed at — the model checks candidates
    and cannot add to them. An answer that is not JSON at all answers nothing,
    and the caller treats the batch as unanswered.
    """
    match = re.search(r"\{.*\}", answer, re.S)
    if not match:
        raise ValueError("no JSON object in the answer")
    answers = json.loads(match.group(0)).get("answers")
    if not isinstance(answers, dict):
        raise ValueError("the answer has no 'answers' mapping")

    out = {number: frozenset[str]() for number in offered}
    for key, letters in answers.items():
        number = int(key) if str(key).strip().isdigit() else None
        if number not in offered or not isinstance(letters, list):
            continue
        out[number] = frozenset(
            offered[number][str(letter).strip()]
            for letter in letters
            if str(letter).strip() in offered[number]
        )
    return out


def batches(questions: list[Question], budget: int) -> list[list[Question]]:
    """Consecutive questions whose rendered text fits ``budget`` characters.

    A single question too large for the budget still gets a batch of its own;
    ``check_outbound`` then refuses it by name rather than this silently
    truncating the utterance.
    """
    out: list[list[Question]] = []
    current: list[Question] = []
    for question in questions:
        trial = [*current, question]
        if current and len(render(trial)[0]) > budget:
            out.append(current)
            current = [question]
        else:
            current = trial
    if current:
        out.append(current)
    return out


def require_scalar_addressing(value: Any, addressing: frozenset[str]) -> None:
    """Refuse a body where an ``addressing`` key holds anything but a string.

    ``check_outbound`` skips the whole value under an addressing key, so the
    exemption is safe only while those keys stay scalars. A copy of module B's
    guard, not an import of it (invariant 2); raised in review of #405.
    """
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in addressing and not isinstance(inner, str):
                raise PrivacyViolationError(
                    f"{key!r} is exempt from the outbound check only as a string"
                )
            require_scalar_addressing(inner, addressing)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            require_scalar_addressing(inner, addressing)


def _client(base_url: str, api_key: str, timeout_sec: float) -> Any:
    """The provider as an ``autune_integrations`` client, the way every outbound
    one is written. The key travels in a header, never in the body or the URL.
    The read timeout is this client's own: a thinking model takes longer than
    the shared 10 s (module B measured 12-20 s a request)."""
    from autune_integrations.base import HttpClient  # noqa: PLC0415

    class VerifierClient(HttpClient):
        service = "gap-template-verifier"
        addressing = frozenset({"role", "responseMimeType"})

        def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            require_scalar_addressing(kwargs.get("json"), self.addressing)
            return super().request(method, path, **kwargs)

    client = VerifierClient(base_url, headers={"x-goog-api-key": api_key})
    client._client.timeout = timeout_sec  # noqa: SLF001 - httpx's own setter; see above
    return client


class GeminiVerifier:
    """Gemini's ``generateContent``, one batch of ambiguous utterances a request."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_sec: float,
        fallback_model: str = "",
    ) -> None:
        if not api_key:
            raise ValueError(
                "AUTUNE_GAP_VERIFIER_IMPL=gemini needs AUTUNE_GAP_VERIFIER_API_KEY. It sends "
                "ambiguous utterances to Google -- see autune_gap.pipeline.verifier."
            )
        self._client = _client(base_url, api_key, timeout_sec)
        self._model = model
        self._fallback = fallback_model
        self.requests = 0
        self.asked: list[Question] = []

    @property
    def model_version(self) -> str:
        """``gemini:<model>``, plus ``+<fallback>`` when one is set — either may
        have answered any batch."""
        return f"gemini:{self._model}" + (f"+{self._fallback}" if self._fallback else "")

    def verify(self, questions: list[Question]) -> list[frozenset[str] | None]:
        self.asked.extend(questions)
        answers: list[frozenset[str] | None] = []
        for index, batch in enumerate(batches(questions, MAX_OUTBOUND_CHARS - _OVERHEAD)):
            answers.extend(self._verify_batch(batch, index=index))
        return answers

    def _verify_batch(self, batch: list[Question], *, index: int) -> list[frozenset[str] | None]:
        text, offered = render(batch)
        body = {
            "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        try:
            parsed = parse(_answer_text(self._post(body, index=index)), offered)
        except (IntegrationError, PrivacyViolationError, ValueError) as exc:
            # The class name only: a provider message or a parse error can echo
            # the request, which is utterances.
            log.warning(
                "gap_verifier_batch_unanswered",
                batch=index,
                questions=len(batch),
                reason=type(exc).__name__,
            )
            return [None] * len(batch)
        return [parsed[number] for number in range(1, len(batch) + 1)]

    def _post_to(self, model: str, body: dict[str, Any], *, index: int) -> Any:
        path = f"/models/{model}:generateContent"
        for attempt, wait in enumerate(_RETRY_BACKOFF_SEC, start=1):
            try:
                self.requests += 1
                return self._client.request("POST", path, json=body)
            except TransientIntegrationError as exc:
                log.info(
                    "gap_verifier_retry", model=model, batch=index, attempt=attempt, reason=str(exc)
                )
                time.sleep(wait)
        self.requests += 1
        return self._client.request("POST", path, json=body)

    def _post(self, body: dict[str, Any], *, index: int) -> Any:
        """The primary model, then the fallback if it stays unavailable. Only a
        transient failure falls back; a refused request fails the same anywhere."""
        try:
            return self._post_to(self._model, body, index=index)
        except TransientIntegrationError:
            if not self._fallback:
                raise
            log.warning(
                "gap_verifier_fallback", model=self._model, fallback=self._fallback, batch=index
            )
            return self._post_to(self._fallback, body, index=index)


def _answer_text(body: Any) -> str:
    """The first candidate's text, or "" -- a blocked or empty answer is not JSON,
    and ``parse`` then leaves the batch unanswered."""
    try:
        parts = body["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    except (KeyError, IndexError, TypeError):
        return ""
