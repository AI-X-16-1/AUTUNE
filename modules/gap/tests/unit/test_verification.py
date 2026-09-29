"""Triage and the verifier's questions — ``verification``, and how ``service._hear``
uses them.

Rankings and vectors are written by hand, so each test names the geometry it is
about. What a real LLM answers is ``python -m autune_gap.eval --probe``'s job.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from autune_core.errors import PrivacyViolationError
from autune_gap import semantic, service, template, verification
from autune_gap.config import get_settings
from autune_gap.pipeline import FakeVerifier
from autune_gap.semantic import BACKGROUND_KEY, Ranking
from autune_gap.verification import Triage

THRESHOLDS = verification.TriageThresholds(
    confident_score=0.6, confident_lead=0.05, candidate_score=0.45, candidates=3
)


def ranking(*classes: tuple[str, float]) -> Ranking:
    return Ranking(classes=tuple(sorted(classes, key=lambda pair: -pair[1])))


def one(ranked: Ranking) -> verification.Decision:
    return verification.triage([ranked], THRESHOLDS)[0]


# --- triage ------------------------------------------------------------------------


def test_a_clear_item_is_confident_and_not_asked() -> None:
    decision = one(ranking(("ownership", 0.72), ("next_step", 0.55), (BACKGROUND_KEY, 0.4)))

    assert decision.triage is Triage.CONFIDENT
    assert decision.item == "ownership"


def test_a_clear_background_is_ignored() -> None:
    """ "네 알겠습니다. 그럼 여기서 마치겠습니다": background 0.86, the nearest
    item 0.65. Asking about it would spend a request, and send a line, to
    confirm what the embedding already knows."""
    decision = one(ranking((BACKGROUND_KEY, 0.86), ("ownership", 0.65), ("dependency", 0.57)))

    assert decision.triage is Triage.IGNORED


def test_a_near_tie_between_items_is_ambiguous() -> None:
    """ "인덱스 재색인이 먼저 끝나야 정렬 로직을 붙일 수 있습니다": next_step
    0.517, dependency 0.511."""
    decision = one(ranking(("next_step", 0.517), ("dependency", 0.511), ("cold_start", 0.487)))

    assert decision.triage is Triage.AMBIGUOUS
    assert decision.candidates == ("next_step", "dependency", "cold_start")


def test_a_clear_win_with_a_low_score_is_ambiguous() -> None:
    """ "인기순 정렬 대신 실시간 개인화로 가는 거죠": cold start by 0.05, at 0.56 —
    over the embedding floor, under the confident score."""
    decision = one(ranking(("cold_start", 0.556), ("error_handling", 0.506), (BACKGROUND_KEY, 0.4)))

    assert decision.triage is Triage.AMBIGUOUS


def test_a_background_win_by_a_hair_is_ambiguous() -> None:
    decision = one(ranking((BACKGROUND_KEY, 0.61), ("ownership", 0.58), ("next_step", 0.5)))

    assert decision.triage is Triage.AMBIGUOUS
    assert decision.candidates == ("ownership", "next_step")


def test_candidates_below_the_candidate_score_are_not_offered() -> None:
    decision = one(ranking(("ownership", 0.5), ("next_step", 0.49), ("risk", 0.3)))

    assert decision.candidates == ("ownership", "next_step")


def test_nothing_near_is_ignored() -> None:
    decision = one(ranking((BACKGROUND_KEY, 0.42), ("ownership", 0.41), ("risk", 0.3)))

    assert decision.triage is Triage.IGNORED


def test_candidates_are_capped() -> None:
    capped = verification.TriageThresholds(
        confident_score=0.9, confident_lead=0.5, candidate_score=0.1, candidates=2
    )
    decision = verification.triage(
        [ranking(("a", 0.5), ("b", 0.49), ("c", 0.48), (BACKGROUND_KEY, 0.2))], capped
    )[0]

    assert decision.candidates == ("a", "b")


# --- questions and answers --------------------------------------------------------


GENERAL = template.get_template("general")


def test_a_question_carries_the_utterance_and_its_candidates_only() -> None:
    decisions = [
        verification.Decision(0, Triage.CONFIDENT, item="ownership"),
        verification.Decision(1, Triage.AMBIGUOUS, candidates=("next_step", "dependency")),
        verification.Decision(2, Triage.IGNORED),
    ]
    speech = ["첫 발화", "둘째 발화", "셋째 발화"]

    asked = verification.questions(decisions, speech, GENERAL, examples_per_candidate=2, limit=10)

    assert [(index, question.utterance) for index, question in asked] == [(1, "둘째 발화")]
    question = asked[0][1]
    assert [candidate.key for candidate in question.candidates] == ["next_step", "dependency"]
    assert all(len(candidate.examples) <= 2 for candidate in question.candidates)


def test_the_limit_bounds_how_many_utterances_are_asked() -> None:
    decisions = [
        verification.Decision(index, Triage.AMBIGUOUS, candidates=("risk",)) for index in range(5)
    ]

    asked = verification.questions(decisions, ["x"] * 5, GENERAL, examples_per_candidate=1, limit=2)

    assert [index for index, _ in asked] == [0, 1]


def test_a_confirmed_candidate_is_heard() -> None:
    decisions = [verification.Decision(0, Triage.AMBIGUOUS, candidates=("dependency", "risk"))]
    asked = verification.questions(decisions, ["발화"], GENERAL, examples_per_candidate=1, limit=5)

    assert verification.resolve(decisions, asked, [frozenset({"dependency"})]) == {"dependency"}


def test_an_answer_cannot_add_an_item_that_was_not_offered() -> None:
    """The verifier checks candidates; it cannot invent one, and it cannot
    promote an item the embedder did not put in front of it."""
    decisions = [verification.Decision(0, Triage.AMBIGUOUS, candidates=("dependency",))]
    asked = verification.questions(decisions, ["발화"], GENERAL, examples_per_candidate=1, limit=5)

    heard = verification.resolve(decisions, asked, [frozenset({"ownership", "made_up_item"})])

    assert heard == frozenset()


def test_confident_items_are_heard_without_an_answer() -> None:
    decisions = [verification.Decision(0, Triage.CONFIDENT, item="ownership")]

    assert verification.resolve(decisions, [], []) == {"ownership"}


# --- service._hear, with a stub embedder -------------------------------------------


class StubEmbedder:
    """Every example sentence of an item embeds to that item's axis; the
    background's to its own. Utterances embed to whatever the test says."""

    model_version = "stub"

    def __init__(self, utterances: dict[str, list[float]]) -> None:
        self._utterances = utterances
        self._axes = {key: position for position, key in enumerate(_AXES)}
        self._example_axis = {
            example: self._axes.get(item.key, self._axes["other"])
            for item in GENERAL.items
            for example in item.examples
        }
        for sentence in semantic.BACKGROUND:
            self._example_axis[sentence] = self._axes[BACKGROUND_KEY]

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            if text in self._utterances:
                out.append(self._utterances[text])
            else:
                vector = [0.0] * len(_AXES)
                vector[self._example_axis[text]] = 1.0
                out.append(vector)
        return out


_AXES = ("ownership", "dependency", "other", BACKGROUND_KEY)


def unit(*values: float) -> list[float]:
    norm = sum(value * value for value in values) ** 0.5
    return [value / norm for value in values]


CLEAR_OWNER = "누가 맡는지 분명한 발화"
TORN = "담당과 의존 사이에서 애매한 발화"
FILLER = "아무것도 정하지 않는 발화"

UTTERANCES = {
    CLEAR_OWNER: unit(0.95, 0.1, 0.0, 0.2),
    TORN: unit(0.55, 0.53, 0.0, 0.3),
    FILLER: unit(0.2, 0.0, 0.0, 0.95),
}


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[FakeVerifier]]:
    """``service._hear`` with the stub embedder and whichever verifier the test
    installs into the returned list."""
    installed: list[FakeVerifier] = []
    monkeypatch.setattr(service, "get_sentence_embedder", lambda: StubEmbedder(UTTERANCES))
    monkeypatch.setattr(
        service, "get_template_verifier", lambda: installed[0] if installed else None
    )
    yield installed


def hear(*speech: str) -> service.Hearing:
    return service._hear(GENERAL, list(speech), get_settings())


def test_only_the_ambiguous_utterance_reaches_the_verifier(wired: list[FakeVerifier]) -> None:
    verifier = FakeVerifier(lambda question: frozenset())
    wired.append(verifier)

    hearing = hear(CLEAR_OWNER, TORN, FILLER)

    assert [question.utterance for question in verifier.asked] == [TORN]
    assert (hearing.confident, hearing.ambiguous, hearing.asked) == (1, 1, 1)


def test_a_rejected_candidate_is_not_heard(wired: list[FakeVerifier]) -> None:
    wired.append(FakeVerifier(lambda question: frozenset()))

    assert hear(CLEAR_OWNER, TORN).heard == {"ownership"}


def test_a_candidate_the_verifier_confirms_is_heard(wired: list[FakeVerifier]) -> None:
    wired.append(FakeVerifier(lambda question: frozenset({"dependency"})))

    assert hear(TORN).heard == {"dependency"}


def test_an_unanswered_question_keeps_the_embedding_answer(wired: list[FakeVerifier]) -> None:
    """The provider failed: the verifier is a check on the embedding, and its
    absence leaves the embedding standing — not "nothing was said"."""
    wired.append(FakeVerifier(lambda question: None))

    hearing = hear(TORN)

    assert hearing.unanswered == 1
    assert hearing.heard == _embedding_only(TORN)


def _embedding_only(text: str) -> frozenset[str]:
    settings = get_settings()
    examples = semantic.examples_for(GENERAL)
    stub = StubEmbedder(UTTERANCES)
    return semantic.heard(
        stub.embed([text]),
        stub.embed(list(examples.texts)),
        examples.labels,
        floor=settings.semantic_floor,
        margin=settings.semantic_margin,
    )


def test_a_privacy_refusal_fails_the_run(wired: list[FakeVerifier]) -> None:
    """``PrivacyViolationError`` is not an unanswered question. It propagates
    out of ``_hear`` and so out of ``detect_gaps``, and the task fails."""

    def refuse(question: verification.Question) -> frozenset[str] | None:
        raise PrivacyViolationError("outbound payload to test contains a phone number")

    wired.append(FakeVerifier(refuse))

    with pytest.raises(PrivacyViolationError):
        hear(TORN)


def test_with_the_verifier_off_the_embedding_decides_alone(wired: list[FakeVerifier]) -> None:
    """The layer below this one, unchanged: same answer as ``semantic.heard``."""
    for text in (CLEAR_OWNER, TORN, FILLER):
        assert hear(text).heard == _embedding_only(text)
