"""The days the approvals card offers for a follow-up meeting (spec section 7)."""

from __future__ import annotations

from datetime import date

from autune_agent.results import ToolResult
from autune_agent.subagents.followup import DateChoice, date_choices, picked_days

RULE = date(2026, 10, 16)


def days_row(*days: object) -> ToolResult:
    return ToolResult(ok=True, summary="", items=[{"title": "다음 회의 날짜", "days": list(days)}])


def test_every_picked_day_is_offered_once_then_the_rules() -> None:
    picked = [date(2026, 10, 22), date(2026, 10, 20), date(2026, 10, 22)]

    assert date_choices(RULE, picked) == [
        DateChoice(date(2026, 10, 20), ("picked",)),
        DateChoice(date(2026, 10, 22), ("picked",)),
        DateChoice(RULE, ("rule",)),
    ]


def test_a_picked_day_that_is_also_the_rules_is_one_choice_saying_both() -> None:
    assert date_choices(RULE, [RULE, date(2026, 10, 20)]) == [
        DateChoice(RULE, ("picked", "rule")),
        DateChoice(date(2026, 10, 20), ("picked",)),
    ]


def test_with_nothing_picked_the_rules_day_is_the_only_choice() -> None:
    assert date_choices(RULE, []) == [DateChoice(RULE, ("rule",))]


def test_a_proposal_without_a_day_offers_only_what_was_picked() -> None:
    assert date_choices(None, [date(2026, 10, 20)]) == [DateChoice(date(2026, 10, 20), ("picked",))]
    assert date_choices(None, []) == []


def test_picked_days_reads_cs_row_and_skips_what_is_not_a_date() -> None:
    result = days_row("2026-10-20", "not a date", 7, "2026-10-22")

    assert picked_days(result) == [date(2026, 10, 20), date(2026, 10, 22)]


def test_a_failed_or_empty_read_is_no_days() -> None:
    assert picked_days(ToolResult.failure("boom", "읽지 못했습니다.")) == []
    assert picked_days(ToolResult(ok=True, summary="", items=[])) == []
