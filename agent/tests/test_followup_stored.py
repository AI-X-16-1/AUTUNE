"""A stored proposal's reason: the template checked before it is kept, and
filled when the approvals card is shown (#1037)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from autune_agent.results import ToolResult
from autune_agent.subagents.followup import card_reason, explain, rules, storable
from autune_agent.subagents.followup.graph import _due, _held, _holidays

MADE_ON = date(2026, 10, 5)
"""A Monday; the proposal was made that day."""

DUE = [
    {"date": "2026-10-13", "confirmed": True, "title": "API 연동"},
    {"date": "2026-10-14", "confirmed": True},
    {"date": "2026-10-15", "confirmed": True},
]
OFF = ["2026-10-09"]
"""한글날."""

HELD = ["2026-09-14T01:00:00+00:00", "2026-09-21T01:00:00+00:00", "2026-09-28T01:00:00+00:00"]


def _result(rows: list[dict[str, Any]], ok: bool = True) -> ToolResult:
    return ToolResult.model_validate({"ok": ok, "summary": "읽음", "items": rows, "evidence": []})


def due_dates(entries: list[dict[str, Any]] | None = None) -> ToolResult:
    return _result([{"title": "기한", "due_dates": DUE if entries is None else entries}])


def holidays() -> ToolResult:
    return _result([{"title": "공휴일", "days": OFF}])


def recent(starts: list[str]) -> ToolResult:
    return _result(
        [
            {"title": "주간 회의", "meeting_id": f"mtg_{n}", "status": "complete", "started_at": s}
            for n, s in enumerate(starts)
        ]
    )


def proposed() -> rules.Suggestion:
    """What Follow-up suggested on ``MADE_ON``: the same reads, the same rule."""
    suggestion = rules.suggest_from_due_dates(_due(due_dates()), MADE_ON, _holidays(holidays()))
    assert suggestion is not None
    assert _why(suggestion).step == "due_share"
    return suggestion


def _why(suggestion: rules.Suggestion) -> rules.Why:
    assert suggestion.why is not None
    return suggestion.why


def test_every_fixed_template_is_storable() -> None:
    """What ``explain`` hands over always passes the check ``main/`` runs again."""
    assert all(storable(template) for template in explain.FALLBACK.values())


@pytest.mark.parametrize(
    "template",
    [
        "",
        "{work}을 {due}까지 마치기로 해서 {date}에 제안했습니다.\n둘째 줄입니다.",
        "{work}을 {due}까지 마치기로 해서 다음 주 {date}에 제안했습니다.",
        "{work}을 10/13까지 마치기로 해서 {date}에 제안했습니다.",
        "{work}을 {due}까지 마치기로 해서 제안했습니다.",
        "{work}을 {due}까지 마치기로 해서 {date}에 {owner}에게 제안했습니다.",
        "{date}에 제안했습니다. {date}입니다.",
        "{date} {due} {work}",
        "{work}을 {due}까지 마치기로 해서 {date}에 제안했습니다 {",
    ],
)
def test_a_template_with_a_date_a_stray_blank_or_no_date_blank_is_not_storable(
    template: str,
) -> None:
    assert not storable(template)
    suggestion = proposed()
    assert (
        card_reason(template, suggestion.day, suggestion.basis, MADE_ON, due_dates(), holidays())
        is None
    )


def test_the_card_reads_the_sentence_the_proposal_wrote() -> None:
    suggestion = proposed()
    template = explain.FALLBACK[explain.fallback_key(suggestion)]

    shown = card_reason(
        template, suggestion.day, suggestion.basis, MADE_ON, due_dates(), holidays()
    )

    assert shown == explain.fill(template, suggestion)
    assert shown is not None and "'API 연동'" in shown and "{" not in shown


def test_a_model_template_is_filled_the_same_way() -> None:
    suggestion = proposed()
    template = (
        "{work}을 {due}까지 마치기로 해서, 그다음 영업일인 {date}에 후속 회의를 제안했습니다."
    )
    assert explain.usable(template, suggestion)

    shown = card_reason(
        template, suggestion.day, suggestion.basis, MADE_ON, due_dates(), holidays()
    )

    assert shown == explain.fill(template, suggestion)


def test_another_day_or_basis_on_the_row_gets_no_sentence() -> None:
    """A date picked from the other choices (#1051) is not the rule's: the
    sentence would explain a day the card does not show."""
    suggestion = proposed()
    template = explain.FALLBACK["due_share"]

    other_day = card_reason(
        template, date(2026, 10, 20), suggestion.basis, MADE_ON, due_dates(), holidays()
    )
    other_basis = card_reason(template, suggestion.day, "draft", MADE_ON, due_dates(), holidays())

    assert other_day is None
    assert other_basis is None


def test_due_dates_changed_since_the_proposal_get_no_sentence() -> None:
    suggestion = proposed()
    later = [{**entry, "date": "2026-10-27"} for entry in DUE]

    shown = card_reason(
        explain.FALLBACK["due_share"],
        suggestion.day,
        suggestion.basis,
        MADE_ON,
        due_dates(later),
        holidays(),
    )

    assert shown is None


def test_a_template_missing_its_steps_blank_is_storable_but_not_shown() -> None:
    """``{work}`` is required for the due-date step; only the run can tell."""
    suggestion = proposed()
    template = "할 일이 거의 끝나는 {due} 다음 영업일인 {date}에 후속 회의를 제안했습니다."
    assert storable(template)

    assert (
        card_reason(template, suggestion.day, suggestion.basis, MADE_ON, due_dates(), holidays())
        is None
    )


def test_a_rhythm_date_is_filled_from_the_meetings_held_by_the_proposal() -> None:
    off = _holidays(holidays())
    suggestion = rules.suggest_by_rhythm(_held(recent(HELD)), MADE_ON, off)
    assert _why(suggestion).step == "cadence"
    template = explain.FALLBACK[explain.fallback_key(suggestion)]
    no_dates = due_dates([])

    shown = card_reason(
        template, suggestion.day, suggestion.basis, MADE_ON, no_dates, holidays(), recent(HELD)
    )
    held_since = card_reason(
        template,
        suggestion.day,
        suggestion.basis,
        MADE_ON,
        no_dates,
        holidays(),
        recent([*HELD, "2026-10-08T01:00:00+00:00"]),
    )
    unread = card_reason(template, suggestion.day, suggestion.basis, MADE_ON, no_dates, holidays())

    assert shown == explain.fill(template, suggestion)
    assert held_since == shown
    assert unread is None
