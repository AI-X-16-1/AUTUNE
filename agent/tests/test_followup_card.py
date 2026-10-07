"""The approvals card's hint from Follow-up (spec section 7, #967 option 2).

The inputs are shaped as B's ``meeting_due_dates`` (#970) and
``public_holidays`` (#995) return them.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from autune_agent.results import ToolResult
from autune_agent.subagents.followup import card_hint, rules

WEDNESDAY = date(2026, 10, 7)


def due_read(*days: str, ok: bool = True) -> ToolResult:
    rows = [{"title": "기한", "due_dates": [{"date": d, "confirmed": True} for d in days]}]
    return ToolResult(ok=ok, summary="기한", items=rows if days else [])


def holiday_read(*days: str, ok: bool = True) -> ToolResult:
    return ToolResult(ok=ok, summary="공휴일", items=[{"title": "공휴일", "days": list(days)}])


def test_it_is_the_date_a_proposal_would_carry() -> None:
    # Due Thursday 10-08; 한글날 Friday 10-09 is off, so Monday 10-12.
    hint = card_hint(due_read("2026-10-08"), holiday_read("2026-10-09"), WEDNESDAY)

    assert hint == rules.Suggestion(date(2026, 10, 12), "confirmed")
    assert hint == rules.suggest_from_due_dates(
        [rules.Due(date(2026, 10, 8), True)], WEDNESDAY, frozenset({date(2026, 10, 9)})
    )


@pytest.mark.parametrize(
    "due",
    [
        # Nothing confirmed with a date yet: B returns no row.
        due_read(),
        # The read failed.
        due_read("2026-10-08", ok=False),
        # Every date past the 14-day horizon.
        due_read("2026-11-30"),
    ],
)
def test_nothing_to_say_is_none(due: ToolResult) -> None:
    assert card_hint(due, holiday_read(), WEDNESDAY) is None


def test_a_failed_holiday_read_counts_weekends_only() -> None:
    hint = card_hint(due_read("2026-10-08"), holiday_read("2026-10-09", ok=False), WEDNESDAY)

    assert hint == rules.Suggestion(date(2026, 10, 9), "confirmed")


def test_the_same_inputs_give_the_same_hint() -> None:
    reads: list[Any] = [due_read("2026-10-12", "2026-10-08"), holiday_read("2026-10-09")]

    assert card_hint(*reads, WEDNESDAY) == card_hint(*reads, WEDNESDAY)
