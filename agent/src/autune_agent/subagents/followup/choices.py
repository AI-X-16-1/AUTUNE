"""The days the approvals card offers for a follow-up meeting (spec section 7).

A Follow-up proposal carries one day, the rule's (``rules.py``). Somebody may
since have picked an event for the next meeting with "다음 회의 잡기" on the
meeting's gap report, and that is a day a person chose. The card offers every
such day, with who picked it, and the rule's, and the approver picks one;
nothing here chooses.

Pure: the card hands in what it already read -- the stored day and C's
``gap.next_meeting_days`` result -- and gets the list back. No tool call, no
clock, no model, nothing stored. A name is what C gives: the display name of
who pressed, which C's team Slack notice already posts for the same press.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

Source = Literal["picked", "rule"]
"""``picked``: an event somebody chose with "다음 회의 잡기". ``rule``: the
day Follow-up's rule suggested, the one the proposal carries."""


@dataclass(frozen=True)
class Picked:
    """One day picked for the next meeting, and who picked it."""

    day: date
    by: tuple[str, ...] = ()


@dataclass(frozen=True)
class DateChoice:
    day: date
    sources: tuple[Source, ...]
    """Why the day is offered; both when a picked day is also the rule's."""
    picked_by: tuple[str, ...] = field(default=())
    """Who picked it, by display name, in C's order; empty for the rule's day."""


def picked_days(result: Any) -> list[Picked]:
    """The days on C's ``next_meeting_days`` row, under ``days`` as
    ``{"day": ISO, "picked_by": [name, ...]}``.

    A failed read, or a row without the list, is no days: the card still
    offers the rule's. An entry whose day is not an ISO date is skipped; a
    name that is not text is left out.
    """
    if not getattr(result, "ok", False):
        return []
    days: list[Picked] = []
    for item in getattr(result, "items", []):
        entries = (getattr(item, "model_extra", None) or {}).get("days")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("day"), str):
                continue
            try:
                day = date.fromisoformat(entry["day"])
            except ValueError:
                continue
            names = entry.get("picked_by")
            by = tuple(n for n in names if isinstance(n, str)) if isinstance(names, list) else ()
            days.append(Picked(day, by))
    return days


def date_choices(rule_day: date | None, picked: Sequence[Picked]) -> list[DateChoice]:
    """Each picked day once, earliest first, with everyone who picked it, then
    the rule's day unless it is one of them -- in which case that choice says
    both.

    The rule's day is last, so a day a person chose reads first. With nothing
    picked, the list is the rule's day alone; with no rule day either (a
    proposal from before #852), it is empty.
    """
    names: dict[date, list[str]] = {}
    for p in picked:
        seen = names.setdefault(p.day, [])
        seen.extend(n for n in p.by if n not in seen)
    choices = [
        DateChoice(
            day,
            ("picked", "rule") if day == rule_day else ("picked",),
            tuple(names[day]),
        )
        for day in sorted(names)
    ]
    if rule_day is not None and rule_day not in names:
        choices.append(DateChoice(rule_day, ("rule",)))
    return choices
