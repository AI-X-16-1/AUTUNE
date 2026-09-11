"""Reciprocal rank fusion — the pure part of hybrid retrieval."""

from __future__ import annotations

from autune_context.pipeline.retrieval import reciprocal_rank_fusion


def test_a_document_ranked_first_everywhere_wins():
    dense = ["m1", "m2", "m3"]
    lexical = ["m1", "m3", "m2"]
    scores = reciprocal_rank_fusion([dense, lexical], k=60)
    assert max(scores, key=scores.get) == "m1"


def test_a_document_missing_from_one_ranking_still_scores():
    scores = reciprocal_rank_fusion([["m1", "m2"], ["m2"]], k=60)
    assert scores["m1"] == 1.0 / 61
    assert scores["m2"] == 1.0 / 62 + 1.0 / 61


def test_larger_k_flattens_the_rank_advantage():
    tight = reciprocal_rank_fusion([["a", "b"]], k=1)
    loose = reciprocal_rank_fusion([["a", "b"]], k=1000)
    assert tight["a"] - tight["b"] > loose["a"] - loose["b"]


def test_empty_rankings_produce_no_scores():
    assert reciprocal_rank_fusion([], k=60) == {}
    assert reciprocal_rank_fusion([[]], k=60) == {}
