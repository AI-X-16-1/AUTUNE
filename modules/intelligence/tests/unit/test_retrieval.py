"""Retrieval over the metric glossary (spec section 5)."""

from __future__ import annotations

from autune_intelligence import glossary, retrieval, tools


def _bm25() -> retrieval.BM25Retriever:
    return retrieval.BM25Retriever(glossary.passages())


def test_a_definition_question_finds_its_passage() -> None:
    top = _bm25().search("결정 밀도 가중치가 얼마야?", k=3)
    assert "quality.weights" in [p.key for p in top]


def test_tokens_leave_out_words_that_match_every_passage() -> None:
    kept = retrieval.tokens("완료율은 어떻게 계산돼? 그건 뭐야?")
    assert "완료율" in kept and "완료" in kept and "계산" in kept
    assert not {"어떻", "되", "뭐", "거", "이"} & set(kept)


def test_a_rate_question_finds_the_rate_not_every_completion_passage() -> None:
    top = [p.key for p in _bm25().search("완료율은 어떻게 계산돼?", k=3)]
    assert "actions.confirmation_vs_completion" in top


def test_rrf_rewards_agreement_between_rankings() -> None:
    # "b" is second in both lists (2 / 62); "a" and "c" are first in one each (1 / 61).
    assert retrieval.rrf([["a", "b"], ["c", "b"]]) == ["b", "a", "c"]
    assert retrieval.rrf([["x"], []]) == ["x"]


def test_an_empty_or_odd_question_returns_without_raising() -> None:
    """Review Focus 4."""
    assert _bm25().search("", k=3) == []
    assert isinstance(_bm25().search("?", k=3), list)
    assert isinstance(_bm25().search("MRR", k=3), list)


def test_explain_metric_returns_passages_within_budget() -> None:
    result = tools.explain_metric(None, "team_x", "완료율은 어떻게 계산돼?")  # type: ignore[arg-type]

    assert result["ok"] is True and 1 <= len(result["items"]) <= 3
    assert all(len(i["body"]) <= 400 for i in result["items"])


def test_explain_metric_says_so_when_nothing_matches() -> None:
    result = tools.explain_metric(None, "team_x", "")  # type: ignore[arg-type]

    assert result["items"] == [] and "찾지 못했습니다" in result["summary"]
