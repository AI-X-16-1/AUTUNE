"""Ranking a team's reports for ``tools.search_reports`` (no database)."""

from __future__ import annotations

from autune_intelligence.retrieval import matching_line, rank_texts


def test_a_team_with_one_report_finds_it_by_its_topic() -> None:
    """``BM25Okapi`` gives every word of a one-text corpus an IDF below zero."""
    assert [i for i, _ in rank_texts("결제 API 얘기한 리포트", ["결제 API 스펙 초안 확정"])] == [0]


def test_a_team_with_two_reports_finds_the_one_that_says_it() -> None:
    """``BM25Okapi`` gives a word in one of two texts an IDF of exactly zero."""
    texts = ["디자인 시안 검토", "결제 API 스펙 초안 확정"]
    assert [i for i, _ in rank_texts("결제 API", texts)] == [1]


def test_the_report_that_says_more_of_it_comes_first() -> None:
    texts = ["결제 일정만 짧게 언급", "온보딩 화면 문구", "결제 API 스펙과 결제 API 오류 처리"]
    ranked = rank_texts("결제 API 오류", texts)
    assert [i for i, _ in ranked] == [2, 0]
    assert ranked[0][1] > ranked[1][1] > 0


def test_a_report_sharing_no_word_is_left_out() -> None:
    assert rank_texts("결제 API", ["디자인 시안 검토", "온보딩 화면 문구"]) == []


def test_an_empty_or_wordless_question_finds_nothing() -> None:
    texts = ["결제 API 스펙"]
    assert rank_texts("", texts) == []
    assert rank_texts("뭐야?", texts) == []
    assert rank_texts("결제", []) == []


def test_the_line_shown_is_the_one_holding_most_of_the_question() -> None:
    line = "✅ 결제 API 오류 코드 정리 — 백엔드"
    text = f"📋 주간 회의 · 10/2\n✅ 결제 일정 공유\n{line}\n🔗 다음 회의"
    assert matching_line("결제 API 오류", text, 160) == line


def test_a_long_line_is_cut_around_the_word_and_says_so() -> None:
    line = "가" * 300 + " 결제 API 오류 " + "나" * 300
    shown = matching_line("결제 API", line, 160)
    assert len(shown) <= 160
    assert "결제 API" in shown
    assert shown.startswith("…") and shown.endswith("…")


def test_without_a_matching_line_the_first_line_is_shown() -> None:
    assert matching_line("결제", "첫 줄\n둘째 줄", 160) == "첫 줄"
    assert matching_line("결제", "", 160) == ""
