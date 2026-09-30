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
- The ambiguous utterances, each on its own numbered line, as module A stored
  them. **Names and numbers said aloud are not masked**: module A has no pattern
  for names, and on the batch path it does not run its spoken-number recogniser,
  so "공일공 일이삼사…" is stored as said (module A's to fix, raised in review
  of #484). Either goes with its line. No speaker, no timestamp, no meeting or
  utterance id, no neighbouring line.

The client, its retry and its fallback are ``gemini``'s, shared with the
relation assistant. Every request goes through
``autune_integrations.HttpClient``, so
``check_outbound`` scans every string in the body and refuses an unmasked phone
number, e-mail or account number written in digits. **That refusal is not
caught here.** It means a stored transcript holds an unmasked value — module
A's masking failed (invariant 11) — and it fails the task rather than falling
back quietly. A question too long to send is never sent, so the size half of
the check cannot be what fires.
"""

from __future__ import annotations

import json
import re
import string
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from autune_core import get_logger
from autune_integrations.errors import IntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

from .gemini import GeminiCaller, answer_text

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

    A single question too large for the budget still gets a batch of its own,
    and ``GeminiVerifier`` leaves it unsent and unanswered rather than
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


class GeminiVerifier(GeminiCaller):
    """Gemini's ``generateContent``, one batch of ambiguous utterances a request."""

    service = "gap-template-verifier"
    event = "gap_verifier"
    key_required = (
        "AUTUNE_GAP_VERIFIER_IMPL=gemini needs AUTUNE_GAP_VERIFIER_API_KEY. It sends "
        "ambiguous utterances to Google -- see autune_gap.pipeline.verifier."
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.asked: list[Question] = []

    def verify(self, questions: list[Question]) -> list[frozenset[str] | None]:
        """Raises ``PrivacyViolationError`` when the outbound check finds an
        unmasked value; every other failure leaves its questions unanswered."""
        self.asked.extend(questions)
        budget = MAX_OUTBOUND_CHARS - _OVERHEAD
        answers: list[frozenset[str] | None] = []
        for index, batch in enumerate(batches(questions, budget)):
            if len(render(batch)[0]) > budget:
                # Only a lone question can be over: one utterance too long to
                # send. Not sending it is the rule; the embedding answers for it.
                log.info("gap_verifier_question_too_long", batch=index)
                answers.extend([None] * len(batch))
                continue
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
            parsed = parse(answer_text(self._post(body, index=index)), offered)
        except (IntegrationError, ValueError) as exc:
            # The provider did not answer usefully: fall back. Not
            # PrivacyViolationError -- that one is an unmasked value in a stored
            # transcript, and it propagates (review of #484).
            #
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
