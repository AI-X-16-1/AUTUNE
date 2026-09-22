"""``service.verify_utterances`` -- step 4 (#12), no session and no real model.

A commitment or ambiguous utterance is re-checked against #12's own
hypothesis; everything else passes through untouched. See ``test_pipeline_
classify.py`` for the same step wired into the task end to end.
"""

from __future__ import annotations

import pytest

from autune_contracts.enums import UtteranceKind
from autune_extraction.decisions import ClassifiedUtterance
from autune_extraction.pipeline.base import NliScores
from autune_extraction.service import COMMITMENT_HYPOTHESIS, verify_utterances

K = UtteranceKind


class StubNli:
    """Answers pairs in the order given, from a fixed script."""

    model_version = "stub"

    def __init__(self, *answers: NliScores) -> None:
        self._answers = list(answers)
        self.seen: list[tuple[str, str]] = []

    def classify(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        self.seen = list(pairs)
        return self._answers


def utterance(
    id_: str, kind: UtteranceKind | None, text: str = "네", confidence: float = 0.5
) -> ClassifiedUtterance:
    return ClassifiedUtterance(id=id_, kind=kind, confidence=confidence, text=text)


def entailed(confidence: float = 0.9) -> NliScores:
    return NliScores(label="entailment", entailment=confidence, contradiction=0.05, neutral=0.05)


def not_entailed(contradiction: float = 0.1, neutral: float = 0.8) -> NliScores:
    return NliScores(
        label="neutral" if neutral >= contradiction else "contradiction",
        entailment=1.0 - contradiction - neutral,
        contradiction=contradiction,
        neutral=neutral,
    )


# --- who gets checked ---------------------------------------------------------


def test_an_entailed_ambiguous_utterance_is_promoted_to_commitment() -> None:
    u = utterance("utt_1", K.AMBIGUOUS, text="내일까지 정리해서 공유드리겠습니다")
    nli = StubNli(entailed(0.93))

    [result] = verify_utterances(nli, [u])

    assert result.kind is K.COMMITMENT
    assert result.nli_verified is True
    assert result.confidence == pytest.approx(0.93)


def test_a_non_entailed_commitment_is_demoted_to_ambiguous() -> None:
    """What read as a promise on its own wording does not survive being read
    against the hypothesis -- #12's confirmation DM is the fallback for it."""
    u = utterance("utt_1", K.COMMITMENT, text="한번 볼게요", confidence=0.8)
    nli = StubNli(not_entailed(contradiction=0.1, neutral=0.75))

    [result] = verify_utterances(nli, [u])

    assert result.kind is K.AMBIGUOUS
    assert result.nli_verified is True
    assert result.confidence == pytest.approx(0.85)


def test_an_entailed_commitment_keeps_its_kind_and_still_gets_verified() -> None:
    u = utterance("utt_1", K.COMMITMENT, confidence=0.6)
    nli = StubNli(entailed(0.97))

    [result] = verify_utterances(nli, [u])

    assert result.kind is K.COMMITMENT
    assert result.nli_verified is True
    assert result.confidence == pytest.approx(0.97), "the stale 5-way confidence is replaced"


def test_a_non_entailed_ambiguous_utterance_keeps_its_kind() -> None:
    u = utterance("utt_1", K.AMBIGUOUS)
    nli = StubNli(not_entailed())

    [result] = verify_utterances(nli, [u])

    assert result.kind is K.AMBIGUOUS
    assert result.nli_verified is True


@pytest.mark.parametrize("kind", [K.DECISION, K.CONCERN, K.OPEN_QUESTION, None])
def test_every_other_kind_passes_through_untouched(kind: UtteranceKind | None) -> None:
    u = utterance("utt_1", kind, confidence=0.42)
    nli = StubNli()

    [result] = verify_utterances(nli, [u])

    assert result is u, "not even rebuilt -- NLI was never asked about it"
    assert result.nli_verified is False


def test_nothing_to_check_makes_no_nli_call() -> None:
    utterances = [utterance("utt_1", K.DECISION), utterance("utt_2", None)]
    nli = StubNli()

    result = verify_utterances(nli, utterances)

    assert result == utterances
    assert nli.seen == []


def test_an_empty_meeting_is_not_an_error() -> None:
    assert verify_utterances(StubNli(), []) == []


# --- the pairing and the order ------------------------------------------------


def test_the_hypothesis_is_12s_own_sentence_for_every_pair() -> None:
    utterances = [
        utterance("utt_1", K.COMMITMENT, text="a"),
        utterance("utt_2", K.AMBIGUOUS, text="b"),
    ]
    nli = StubNli(entailed(), entailed())

    verify_utterances(nli, utterances)

    assert nli.seen == [("a", COMMITMENT_HYPOTHESIS), ("b", COMMITMENT_HYPOTHESIS)]


def test_order_and_untouched_rows_are_preserved_around_the_checked_ones() -> None:
    utterances = [
        utterance("utt_1", K.DECISION),
        utterance("utt_2", K.COMMITMENT),
        utterance("utt_3", None),
        utterance("utt_4", K.AMBIGUOUS),
    ]
    nli = StubNli(entailed(), not_entailed())

    result = verify_utterances(nli, utterances)

    assert [u.id for u in result] == ["utt_1", "utt_2", "utt_3", "utt_4"]
    assert result[0].kind is K.DECISION and result[0].nli_verified is False
    assert result[1].kind is K.COMMITMENT and result[1].nli_verified is True
    assert result[2].kind is None and result[2].nli_verified is False
    assert result[3].kind is K.AMBIGUOUS and result[3].nli_verified is True


def test_a_relabelled_utterance_keeps_its_id_text_and_speaker() -> None:
    u = ClassifiedUtterance(
        id="utt_1", kind=K.AMBIGUOUS, confidence=0.4, text="한번 볼게요", speaker="화자 1"
    )
    nli = StubNli(entailed())

    [result] = verify_utterances(nli, [u])

    assert result.id == "utt_1"
    assert result.text == "한번 볼게요"
    assert result.speaker == "화자 1"


# --- the model's own contract is honoured -------------------------------------


def test_a_short_answer_from_the_model_is_refused() -> None:
    """The Protocol promises one result per input, in order; zipping a short
    list would verify the wrong utterance without an error."""
    utterances = [utterance("utt_1", K.COMMITMENT), utterance("utt_2", K.AMBIGUOUS)]
    nli = StubNli(entailed())

    with pytest.raises(ValueError, match="asked for 2 NLI results"):
        verify_utterances(nli, utterances)
