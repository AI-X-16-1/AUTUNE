"""Weekly report content — pure functions, no database.

LLM prose generation is out of scope for now (see docs/modules/intelligence.md
step 6) — the body is a deterministic template over already-computed numbers,
with a clear seam for an LLM to replace it later. No text here is derived from
transcript content; every value is a number already aggregated in intel_scores
/ intel_gap_patterns.
"""

from __future__ import annotations

from datetime import date

from autune_intelligence import service


def test_body_is_a_no_meetings_message_when_nothing_was_scored() -> None:
    body = service._report_body_markdown(
        period_start=date(2026, 9, 7),
        period_end=date(2026, 9, 14),
        meeting_count=0,
        average_value=None,
        grade_distribution={},
        gap_distribution={},
        action_item_completion_rate=None,
    )

    assert "2026-09-07" in body
    assert "2026-09-14" in body
    assert "분석된 회의가 없습니다" in body


def test_body_reports_meeting_count_and_average_grade() -> None:
    body = service._report_body_markdown(
        period_start=date(2026, 9, 7),
        period_end=date(2026, 9, 14),
        meeting_count=4,
        average_value=0.82,
        grade_distribution={"A": 2, "B": 2},
        gap_distribution={},
        action_item_completion_rate=None,
    )

    assert "4건" in body
    assert "B" in body  # _grade_for(0.82) == "B" (cutoff is >= 0.9 for "A")


def test_body_names_the_most_common_gap_type() -> None:
    body = service._report_body_markdown(
        period_start=date(2026, 9, 7),
        period_end=date(2026, 9, 14),
        meeting_count=3,
        average_value=0.7,
        grade_distribution={"B": 3},
        gap_distribution={"ownership": 5, "schedule": 2},
        action_item_completion_rate=None,
    )

    assert "ownership" in body
    assert "5건" in body


def test_body_omits_the_gap_line_when_no_gaps_were_found() -> None:
    body = service._report_body_markdown(
        period_start=date(2026, 9, 7),
        period_end=date(2026, 9, 14),
        meeting_count=2,
        average_value=0.9,
        grade_distribution={"A": 2},
        gap_distribution={},
        action_item_completion_rate=None,
    )

    assert "갭" not in body


def test_body_includes_action_item_completion_rate_when_measured() -> None:
    body = service._report_body_markdown(
        period_start=date(2026, 9, 7),
        period_end=date(2026, 9, 14),
        meeting_count=2,
        average_value=0.9,
        grade_distribution={"A": 2},
        gap_distribution={},
        action_item_completion_rate=0.5,
    )

    assert "50%" in body


def test_body_omits_the_completion_line_when_not_measured() -> None:
    body = service._report_body_markdown(
        period_start=date(2026, 9, 7),
        period_end=date(2026, 9, 14),
        meeting_count=2,
        average_value=0.9,
        grade_distribution={"A": 2},
        gap_distribution={},
        action_item_completion_rate=None,
    )

    assert "완료율" not in body
