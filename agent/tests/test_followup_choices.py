"""The days the approvals card offers for a follow-up meeting (spec section 7)."""

from __future__ import annotations

from datetime import date

from autune_agent.main.ask import MEETING_TOOLS, TEAM_TOOLS
from autune_agent.results import ToolResult
from autune_agent.subagents.followup import DateChoice, Picked, date_choices, picked_days

RULE = date(2026, 10, 16)
OCT_20 = date(2026, 10, 20)
OCT_22 = date(2026, 10, 22)


def days_row(*days: object) -> ToolResult:
    return ToolResult(ok=True, summary="", items=[{"title": "다음 회의 날짜", "days": list(days)}])


def test_every_picked_day_is_offered_once_with_who_picked_it_then_the_rules() -> None:
    picked = [Picked(OCT_22, ("이지은",)), Picked(OCT_20, ("김민수",)), Picked(OCT_22, ("박서준",))]

    assert date_choices(RULE, picked) == [
        DateChoice(OCT_20, ("picked",), ("김민수",)),
        DateChoice(OCT_22, ("picked",), ("이지은", "박서준")),
        DateChoice(RULE, ("rule",)),
    ]


def test_a_picked_day_that_is_also_the_rules_is_one_choice_saying_both() -> None:
    assert date_choices(RULE, [Picked(RULE, ("김민수",)), Picked(OCT_20, ("이지은",))]) == [
        DateChoice(RULE, ("picked", "rule"), ("김민수",)),
        DateChoice(OCT_20, ("picked",), ("이지은",)),
    ]


def test_with_nothing_picked_the_rules_day_is_the_only_choice() -> None:
    assert date_choices(RULE, []) == [DateChoice(RULE, ("rule",))]


def test_a_proposal_without_a_day_offers_only_what_was_picked() -> None:
    assert date_choices(None, [Picked(OCT_20, ("김민수",))]) == [
        DateChoice(OCT_20, ("picked",), ("김민수",))
    ]
    assert date_choices(None, []) == []


def test_picked_days_reads_cs_row_and_skips_what_is_not_a_day() -> None:
    result = days_row(
        {"day": "2026-10-20", "picked_by": ["김민수", 7]},
        {"day": "not a date", "picked_by": ["x"]},
        "2026-10-21",
        {"day": "2026-10-22"},
    )

    assert picked_days(result) == [Picked(OCT_20, ("김민수",)), Picked(OCT_22, ())]


def test_a_failed_or_empty_read_is_no_days() -> None:
    assert picked_days(ToolResult.failure("boom", "읽지 못했습니다.")) == []
    assert picked_days(ToolResult(ok=True, summary="", items=[])) == []


def test_the_picked_days_and_their_names_are_not_offered_to_the_chat_model() -> None:
    # A picked day carries who picked it; the chat model must not get that name.
    assert "gap.next_meeting_days" not in MEETING_TOOLS + TEAM_TOOLS
