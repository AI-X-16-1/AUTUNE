"""The topic-linking eval's threshold sweep: it re-derives ``asserted`` from the
stored scores, so it must hold back what the keyphrase check held back."""

from __future__ import annotations

from autune_context.config import get_settings
from autune_context.eval.topic_linking.runner import CaseResult, _held_back


def _result(
    *, asserted: set[str], similarity: dict[str, float], rerank: dict[str, float]
) -> CaseResult:
    asserted_set = frozenset(asserted)
    return CaseResult(
        case_id="c",
        category="no_link",
        expected=frozenset(),
        asserted=asserted_set,
        pending=frozenset(similarity) - asserted_set,
        best_similarity=similarity,
        best_rerank=rerank,
        past_ids=tuple(similarity),
        held_back=_held_back(asserted_set, similarity, rerank),
    )


def test_held_back_after_the_reranker_alone_cleared_it_stays_out() -> None:
    s = get_settings()
    # Re-ranker 1.0 clears link_confidence_threshold; the keyphrase check said no.
    r = _result(asserted=set(), similarity={"m": 0.50}, rerank={"m": 1.0})

    assert r.held_back == {"m"}
    assert r.asserted_at(s.link_similarity_threshold, s.link_confidence_threshold) == r.asserted
    assert r.asserted_at(0.50, s.link_confidence_threshold) == frozenset()


def test_held_back_after_dense_similarity_alone_cleared_it_stays_out() -> None:
    s = get_settings()
    r = _result(asserted=set(), similarity={"m": 0.90}, rerank={"m": 0.0})

    assert r.held_back == {"m"}
    assert r.asserted_at(0.50, s.link_confidence_threshold) == frozenset()


def test_at_the_configured_thresholds_the_sweep_reproduces_what_was_asserted() -> None:
    s = get_settings()
    similarity = {"asserted": 0.90, "held": 0.80, "reranked": 0.40, "low": 0.40}
    rerank = {"asserted": 0.10, "held": 1.0, "reranked": 0.95, "low": 0.10}
    r = _result(asserted={"asserted", "reranked"}, similarity=similarity, rerank=rerank)

    assert r.held_back == {"held"}
    assert r.asserted_at(s.link_similarity_threshold, s.link_confidence_threshold) == r.asserted


def test_below_the_configured_threshold_an_unchecked_meeting_counts_as_passing() -> None:
    s = get_settings()
    r = _result(asserted=set(), similarity={"m": 0.60}, rerank={"m": 0.0})

    assert r.held_back == frozenset()
    assert r.asserted_at(0.60, s.link_confidence_threshold) == {"m"}
