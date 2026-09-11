"""The pure parts of decision-lineage building: NLI-label mapping and matching."""

from __future__ import annotations

from autune_context.service import _NLI_TO_CHANGE, _cosine, _match_thread, _ThreadHead
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
    return _ThreadHead(thread_id, latest=object(), vector=vector)  # type: ignore[arg-type]


def test_match_thread_picks_the_most_similar_above_the_threshold() -> None:
    heads = [_head("thr_a", [1.0, 0.0]), _head("thr_b", [0.0, 1.0])]
    assert _match_thread([0.9, 0.1], heads, used=set(), threshold=0.6).thread_id == "thr_a"


def test_match_thread_returns_none_below_the_threshold() -> None:
    heads = [_head("thr_a", [1.0, 0.0])]
    assert _match_thread([0.0, 1.0], heads, used=set(), threshold=0.6) is None


def test_match_thread_skips_threads_already_used_this_run() -> None:
    heads = [_head("thr_a", [1.0, 0.0]), _head("thr_b", [1.0, 0.0])]
    assert _match_thread([1.0, 0.0], heads, used={"thr_a"}, threshold=0.6).thread_id == "thr_b"
