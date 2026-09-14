"""The pure parts of decision-lineage building: NLI-label mapping and matching."""

from __future__ import annotations

from autune_context.service import (
    _NLI_TO_CHANGE,
    _assign_decisions_to_threads,
    _cosine,
    _ThreadHead,
)
from autune_contracts import ChangeType, NliLabel


def test_every_nli_label_maps_to_a_change_type() -> None:
    assert _NLI_TO_CHANGE == {
        NliLabel.ENTAILMENT: ChangeType.UNCHANGED,
        NliLabel.CONTRADICTION: ChangeType.REVERSED,
        NliLabel.NEUTRAL: ChangeType.MODIFIED,
    }
    # `new` is not an NLI outcome — it is the no-match case.
    assert set(_NLI_TO_CHANGE.values()) == {
        ChangeType.UNCHANGED,
        ChangeType.REVERSED,
        ChangeType.MODIFIED,
    }


def test_cosine_is_1_for_identical_and_0_for_orthogonal() -> None:
    assert _cosine([1.0, 0.0], [2.0, 0.0]) == 1.0
    assert _cosine([1.0, 0.0], [0.0, 5.0]) == 0.0
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero vector, no div by zero


def _head(thread_id: str, vector: list[float]) -> _ThreadHead:
    return _ThreadHead(thread_id, vector)


def test_assign_picks_the_most_similar_above_the_threshold() -> None:
    heads = [_head("thr_a", [1.0, 0.0]), _head("thr_b", [0.0, 1.0])]
    assignment = _assign_decisions_to_threads([[0.9, 0.1]], heads, threshold=0.6)
    assert assignment[0].thread_id == "thr_a"


def test_assign_leaves_a_decision_unassigned_below_the_threshold() -> None:
    heads = [_head("thr_a", [1.0, 0.0])]
    assert _assign_decisions_to_threads([[0.0, 1.0]], heads, threshold=0.6) == {}


def test_assign_is_one_to_one() -> None:
    # Both decisions are an equally strong match for both threads; every valid
    # assignment must still be a bijection — no thread taken twice.
    heads = [_head("thr_a", [1.0, 0.0]), _head("thr_b", [1.0, 0.0])]
    vectors = [[1.0, 0.0], [1.0, 0.0]]
    assignment = _assign_decisions_to_threads(vectors, heads, threshold=0.6)
    assert len(assignment) == 2
    assert {head.thread_id for head in assignment.values()} == {"thr_a", "thr_b"}


def test_assign_treats_duplicate_thread_id_entries_as_one_slot() -> None:
    """``heads`` can list the same ``thread_id`` twice — once as the team-wide
    head ``_thread_heads`` found, once as a reprocessed meeting's own
    about-to-be-replaced version for that same thread
    (``build_decision_lineage``). The two entries are candidates for one slot,
    not two: two different decisions must not both land on ``thr_a`` just
    because it appears twice in ``heads``."""
    heads = [
        _head("thr_a", [1.0, 0.0]),  # e.g. the team-wide head
        _head("thr_a", [0.0, 1.0]),  # e.g. this meeting's own old version
    ]
    vectors = [[1.0, 0.0], [0.0, 1.0]]  # two decisions, each a perfect match for one entry
    assignment = _assign_decisions_to_threads(vectors, heads, threshold=0.6)
    assert len(assignment) == 1  # only one decision can take thr_a, not both


def test_assign_prefers_the_stronger_match_regardless_of_input_order() -> None:
    """The bug this replaces: a weak match earlier in the decision list could
    grab a thread out from under a much stronger match later in it. With only
    one candidate thread, the weak decision must lose it either way."""
    heads = [_head("thr_a", [1.0, 0.0])]
    strong = [0.99, 0.01]  # cosine ~0.9999 to thr_a
    weak = [0.7, 0.71]  # cosine ~0.70 to thr_a — still clears a 0.6 threshold

    weak_first = _assign_decisions_to_threads([weak, strong], heads, threshold=0.6)
    assert weak_first.get(0) is None
    assert weak_first[1].thread_id == "thr_a"

    strong_first = _assign_decisions_to_threads([strong, weak], heads, threshold=0.6)
    assert strong_first[0].thread_id == "thr_a"
    assert strong_first.get(1) is None
