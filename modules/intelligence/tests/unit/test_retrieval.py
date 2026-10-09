"""Retrieval over the metric glossary (spec section 5)."""

from __future__ import annotations

import pytest

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


def test_a_question_about_a_closed_item_gets_the_answer() -> None:
    """B leaves an item closed without being finished out of its counts (#993)."""
    [top] = _bm25().search("끝내지 않고 닫은 항목도 완료율에 들어가?", k=1)
    assert top.key == "actions.confirmation_vs_completion"
    assert "닫은 항목은 확정된 항목으로 세지 않아서" in top.text


@pytest.mark.parametrize(
    ("question", "key", "says"),
    [
        ("슬랙 연결 안 하면 리포트 게시돼?", "reports.slack", "게시 카드는 만들지 않으며"),
        ("팀원 발언 비율 볼 수 있어?", "quality.speaking_ratio", "본인에게만"),
        ("방금 한 회의 점수는 왜 없어?", "quality.pending", "10분"),
        ("히트맵이 왜 비어 있어?", "alignment.floor", "3명 이상이 화자로 확인된"),
        (
            "여러 팀 주간 리포트 시간을 한 번에 바꿀 수 있어?",
            "reports.weekly_schedule",
            "내 모든 팀",
        ),
    ],
    ids=["slack", "speaking-ratio", "pending", "heatmap-people", "many-teams"],
)
def test_what_changed_lately_is_explained(question: str, key: str, says: str) -> None:
    """Passages for #1000/#1004, privacy.md 3, the aggregation wait, #999 and #1155."""
    [top] = _bm25().search(question, k=1)
    assert top.key == key
    assert says in top.text


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
