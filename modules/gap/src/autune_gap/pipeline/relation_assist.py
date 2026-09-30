"""Step 2's hard cases, asked of a model: the pairs the rules decline to read.

``relations.RuleRelations`` runs first and everything it asserts stands. Then,
for the adjacent mention pairs ``relations.hard_pairs`` names — ``는데``/``지만``
glue, a bare ``의``, and a reason the resolution guard may have refused — a
model is asked whether the utterance states a relation between the two, and
which. That is the LLM assistance ``docs/modules/gap.md`` step 2 promised and
issue #32 asked for, kept to the cases the rules already named as theirs.

**What the model may answer.** Only a pair it was shown, in either direction,
with one of ``base.RELATION_LABELS``. Anything else in the answer is dropped.
It cannot touch a pair the rules typed, cannot add a topic, and cannot relate a
mention to itself. A symmetric relation it names is written both ways, as the
rules write theirs.

**What the Gemini implementation sends** — per request, never more than
``MAX_OUTBOUND_CHARS``:

- ``INSTRUCTIONS``, fixed text.
- Each asked utterance on its own numbered line, as module A stored it, with
  the mentions of its pairs lettered beside it. The mentions are substrings of
  that line, so nothing leaves that the line did not already carry. **Names and
  numbers said aloud are not masked** (see ``verifier``, the same exposure). No
  speaker, timestamp, meeting or utterance id, and no neighbouring line.

Only utterances with a hard pair are sent, and at most
``AUTUNE_GAP_RELATION_ASSIST_MAX_UTTERANCES`` of one meeting. When the provider
cannot answer, the rules' answer stands for that batch. ``PrivacyViolationError``
from the outbound check is raised, not answered, for the reason ``verifier``
gives (review of #484).
"""

from __future__ import annotations

import json
import re
import string
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from autune_core import get_logger
from autune_integrations.errors import IntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

from .base import RELATION_LABELS, SYMMETRIC_RELATIONS, Entity, Relation
from .gemini import GeminiCaller, answer_text
from .relations import RuleRelations, hard_pairs, read_utterance, relation_names

log = get_logger(__name__)

Triple = tuple[str, str, str]
"""``(source, relation, target)`` as the model states it, by mention text."""

MAX_PAIRS = 12
"""Pairs asked about in one utterance, at most. Each mention of them gets a
letter, and a run-on utterance naming thirty topics is a transcript problem to
look at, not thirty-odd questions to send."""


@dataclass(frozen=True)
class PairQuestion:
    """One utterance and the pairs in it the rules declined to read."""

    utterance: str
    pairs: tuple[tuple[str, str], ...]


class RelationAsker(Protocol):
    """Answers, per question, the triples the utterance states among its pairs,
    or ``None`` when that question could not be answered. Raises
    ``PrivacyViolationError`` rather than answering, as ``TemplateVerifier``
    does."""

    @property
    def model_version(self) -> str: ...

    def ask(self, questions: list[PairQuestion]) -> list[frozenset[Triple] | None]: ...


class FakeRelationAsker:
    """No network, deterministic. Answers from ``decide`` when given one, and
    otherwise asserts nothing — a fake that invented relations would be a rule
    nobody wrote. Records what it was asked, so a test can assert what would
    have been sent."""

    model_version = "fake"

    def __init__(self, decide: Callable[[PairQuestion], frozenset[Triple] | None] | None = None):
        self._decide = decide
        self.asked: list[PairQuestion] = []

    def ask(self, questions: list[PairQuestion]) -> list[frozenset[Triple] | None]:
        self.asked.extend(questions)
        if self._decide is None:
            return [frozenset() for _ in questions]
        return [self._decide(question) for question in questions]


class AssistedRelations:
    """``RuleRelations``, plus a model for the pairs the rules decline.

    Every rule relation comes first in what ``extract`` returns, so a triple
    both found is attributed to the rule (``graph.relation_edges`` keeps the
    first). Each relation carries ``asserted_by``, and ``gap_topic_edges``
    records it: an edge the model added can always be told from one a rule read.
    """

    def __init__(self, rules: RuleRelations, asker: RelationAsker, *, max_utterances: int) -> None:
        self._rules = rules
        self._asker = asker
        self._max = max_utterances

    @property
    def model_version(self) -> str:
        return f"{self._rules.model_version}+{self._asker.model_version}"

    @property
    def asker(self) -> RelationAsker:
        """What answers the hard pairs — for the eval, which reports its load."""
        return self._asker

    def extract(self, utterances: list[tuple[str, str]], entities: list[Entity]) -> list[Relation]:
        names = relation_names(entities)
        rules = self._rules.model_version
        found: list[Relation] = []
        asking: list[tuple[str, PairQuestion]] = []
        for utterance_id, text in utterances:
            spans, typed = read_utterance(text, names)
            found.extend(
                Relation(source, target, relation, utterance_id, asserted_by=rules)
                for source, target, relation in typed
            )
            pairs = hard_pairs(text, spans, typed)
            if pairs:
                asking.append((utterance_id, PairQuestion(text, tuple(pairs[:MAX_PAIRS]))))

        held_back = max(0, len(asking) - self._max)
        asking = asking[: self._max]
        answers = self._asker.ask([question for _, question in asking]) if asking else []

        added = 0
        model = self._asker.model_version
        for (utterance_id, _), answer in zip(asking, answers, strict=True):
            for source, relation, target in sorted(answer or ()):
                found.append(Relation(source, target, relation, utterance_id, asserted_by=model))
                if relation in SYMMETRIC_RELATIONS:
                    found.append(
                        Relation(target, source, relation, utterance_id, asserted_by=model)
                    )
                added += 1

        # Counts only: an utterance is transcript text, and no log line carries any.
        log.info(
            "gap_relation_assist",
            asked=len(asking),
            held_back=held_back,
            unanswered=sum(1 for answer in answers if answer is None),
            added=added,
        )
        return found


INSTRUCTIONS = (
    "회의 발화 한 줄이 두 주제 사이에 어떤 관계를 말했는지 판정하세요.\n"
    "관계는 아래 넷 중 하나이고, [X글자, 관계, Y글자] 로 씁니다.\n"
    "- depends_on: X 를 하려면 Y 가 먼저 있어야 한다\n"
    "- blocked_by: Y 때문에 X 가 지금 진행되지 못한다\n"
    "- part_of: X 가 Y 를 이루는 일부다 (담당·소유·일정은 아님)\n"
    "- alternative_to: X 와 Y 를 서로 대안으로 견준다\n"
    "- 각 [발화]는 따로 판단하고, 그 줄의 '쌍'에 적힌 두 주제 사이만 판단하세요.\n"
    "- 발화가 그 관계를 분명히 말했을 때만 답하세요. 한 문장에 같이 나왔다는 것만으로는 "
    "관계가 아닙니다.\n"
    "- 부정하거나, 묻거나, 이미 해결됐다고 말한 관계는 답하지 마세요. "
    "확실하지 않으면 비워 두세요.\n"
    'JSON 한 줄로만 답하세요: {"answers": {"발화번호": [["X글자", "관계", "Y글자"], ...]}}'
)
"""The whole of what the model is told. Korean, because the utterances are;
short, because ``check_outbound`` counts it against the same 4,000 characters.
The direction of each label is the one ``base.RELATION_LABELS`` and the rules
use: ``source relation target``."""

_LETTERS = string.ascii_uppercase
_OVERHEAD = len(INSTRUCTIONS) + 300
"""Instructions plus JSON punctuation and markers the budget leaves room for."""


def render(questions: list[PairQuestion]) -> tuple[str, dict[int, dict[str, str]]]:
    """The request text, and line number -> {letter: mention} for each line.

    Letters restart on every line: a mention is lettered beside the utterance
    it was said in, and ``parse`` resolves a letter against that line only.
    """
    lines: list[str] = []
    lettered: dict[int, dict[str, str]] = {}
    for number, question in enumerate(questions, start=1):
        letter_of: dict[str, str] = {}
        for pair in question.pairs:
            for mention in pair:
                letter_of.setdefault(mention, _LETTERS[len(letter_of)])
        lettered[number] = {letter: mention for mention, letter in letter_of.items()}
        topics = ", ".join(f"{letter}={mention}" for mention, letter in letter_of.items())
        pairs = ", ".join(f"{letter_of[left]}-{letter_of[right]}" for left, right in question.pairs)
        lines.append(f"[발화 {number}] {question.utterance}")
        lines.append(f"  주제: {topics}")
        lines.append(f"  쌍: {pairs}")
    return "\n".join(lines), lettered


def parse(
    answer: str, questions: list[PairQuestion], lettered: dict[int, dict[str, str]]
) -> dict[int, frozenset[Triple]]:
    """``{"answers": {"1": [["B", "blocked_by", "A"]]}}`` -> triples by mention.

    Kept only when both ends are on that line, the two form a pair the line
    offered (either direction), and the label is one of ``RELATION_LABELS``.
    Anything else is dropped rather than guessed at. A line the answer skips
    states nothing. An answer that is not the JSON shape raises ``ValueError``
    and the caller leaves the batch unanswered.
    """
    match = re.search(r"\{.*\}", answer, re.S)
    if not match:
        raise ValueError("no JSON object in the answer")
    answers = json.loads(match.group(0)).get("answers")
    if not isinstance(answers, dict):
        raise ValueError("the answer has no 'answers' mapping")

    out = {number: frozenset[Triple]() for number in lettered}
    for key, stated in answers.items():
        number = int(key) if str(key).strip().isdigit() else None
        if number not in lettered or not isinstance(stated, list):
            continue
        offered = {frozenset(pair) for pair in questions[number - 1].pairs}
        kept: set[Triple] = set()
        for triple in stated:
            if not isinstance(triple, list) or len(triple) != 3:
                continue
            left, relation, right = (str(part).strip() for part in triple)
            source, target = _mention(lettered[number], left), _mention(lettered[number], right)
            if source is None or target is None or relation not in RELATION_LABELS:
                continue
            if frozenset((source, target)) not in offered or source == target:
                continue
            kept.add((source, relation, target))
        out[number] = frozenset(kept)
    return out


def _mention(lettered: dict[str, str], said: str) -> str | None:
    """The mention an answer names, by its letter or by the mention itself.

    Both, because the model does both. Asked for letters, ``gemini-3.5-flash``
    and ``gemini-3.8-flash`` answered every probe with the topic names instead,
    and a parser that took letters only dropped all of them — eight probes,
    zero relations, indistinguishable from a model that found none. A name is
    accepted only if it is one of this line's own mentions, so it widens
    nothing the letters did not already offer.

    **A string that is a letter for one mention and the name of another is
    refused.** A topic called ``B`` beside a second mention lettered ``B``
    cannot be told apart, and a wrong end is worse than no relation. Raised in
    review of #499.
    """
    by_letter = lettered.get(said)
    by_name = said if said in lettered.values() else None
    if by_letter is not None and by_name is not None and by_letter != by_name:
        return None
    return by_letter or by_name


def batches(questions: list[PairQuestion], budget: int) -> list[list[PairQuestion]]:
    """Consecutive questions whose rendered text fits ``budget`` characters. A
    single question too large still gets a batch of its own, and is not sent."""
    out: list[list[PairQuestion]] = []
    current: list[PairQuestion] = []
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


class GeminiRelationAsker(GeminiCaller):
    """Gemini's ``generateContent``, one batch of hard-pair utterances a request."""

    service = "gap-relation-assist"
    event = "gap_relation_assist"
    key_required = (
        "AUTUNE_GAP_RELATION_IMPL=gemini needs AUTUNE_GAP_VERIFIER_API_KEY. It sends the "
        "utterances the relation rules decline to Google -- see "
        "autune_gap.pipeline.relation_assist."
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.asked: list[PairQuestion] = []

    def ask(self, questions: list[PairQuestion]) -> list[frozenset[Triple] | None]:
        self.asked.extend(questions)
        budget = MAX_OUTBOUND_CHARS - _OVERHEAD
        answers: list[frozenset[Triple] | None] = []
        for index, batch in enumerate(batches(questions, budget)):
            if len(render(batch)[0]) > budget:
                log.info("gap_relation_assist_question_too_long", batch=index)
                answers.extend([None] * len(batch))
                continue
            answers.extend(self._ask_batch(batch, index=index))
        return answers

    def _ask_batch(
        self, batch: list[PairQuestion], *, index: int
    ) -> list[frozenset[Triple] | None]:
        text, lettered = render(batch)
        body = {
            "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        try:
            parsed = parse(answer_text(self._post(body, index=index)), batch, lettered)
        except (IntegrationError, ValueError) as exc:
            # The rules' answer stands. Not PrivacyViolationError, which
            # propagates. The class name only: a provider message or a parse
            # error can echo the request, which is utterances.
            log.warning(
                "gap_relation_assist_batch_unanswered",
                batch=index,
                questions=len(batch),
                reason=type(exc).__name__,
            )
            return [None] * len(batch)
        return [parsed[number] for number in range(1, len(batch) + 1)]
