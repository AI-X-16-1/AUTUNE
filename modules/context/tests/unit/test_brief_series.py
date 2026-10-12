"""Which titles the pre-meeting brief reads as one series (``briefs._series_key``)."""

from __future__ import annotations

import pytest

from autune_context.briefs import _series_key


@pytest.mark.parametrize(
    ("earlier", "later"),
    [
        ("v2.0 출시 준비 1차 회의", "v2.0 출시 준비 2차 회의"),
        ("물류센터 이전 1차회의", "물류센터 이전 2차회의"),
        ("연말 행사 준비", "연말 행사 준비 2차"),
        ("신제품 네이밍 1회차 워크숍", "신제품 네이밍 2회차 워크숍"),
        ("QA 점검 4주차", "QA 점검 5주차"),
        ("10월 1주차 운영 회의", "10월 2주차 운영 회의"),
        ("투자사 미팅 첫 번째", "투자사 미팅 두 번째"),
        ("고객 자문단 2번째 모임", "고객 자문단 3번째 모임"),
        ("스프린트 15 회고", "스프린트 16 회고"),
        ("Sprint 23 Planning", "sprint 24 planning"),
        ("9월 마케팅 월간 회의", "10월 마케팅 월간 회의"),
        ("3분기 사업 리뷰", "4분기 사업 리뷰"),
        ("10/7 주간회의", "10/14 주간회의"),
        ("2026.10.07 경영진 회의", "2026-10-14 경영진 회의"),
        ("제품 리뷰 #12", "제품 리뷰 #13"),
    ],
)
def test_a_round_number_or_date_does_not_split_a_series(earlier: str, later: str) -> None:
    assert _series_key(earlier) == _series_key(later)


@pytest.mark.parametrize(
    ("one", "other"),
    [
        # A bare number names a group, not a round.
        ("1팀 주간회의", "2팀 주간회의"),
        ("고객사 1 미팅", "고객사 2 미팅"),
        # A version is a different release.
        ("v2.0 출시 준비 회의", "v3.0 출시 준비 회의"),
        ("Sprint 23 Planning", "Sprint 23 Review"),
    ],
)
def test_other_numbers_keep_titles_apart(one: str, other: str) -> None:
    assert _series_key(one) != _series_key(other)


def test_a_title_that_is_only_a_date_has_no_key() -> None:
    assert _series_key("10/14") == ""
