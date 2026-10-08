"""The days the approvals card offers for a follow-up meeting (spec section 7).

A Follow-up proposal carries one day, the rule's (``rules.py``). Somebody may
since have picked an event for the next meeting with "다음 회의 잡기" on the
meeting's gap report, and that is a day a person chose. The card offers every
such day and the rule's, and the approver picks one; nothing here chooses.

Pure: the card hands in what it already read -- the stored day and C's
``gap.next_meeting_days`` result -- and gets the list back. No tool call, no
clock, no model, nothing stored. Days only: C's read names nobody, and neither
does a choice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

Source = Literal["picked", "rule"]
"""``picked``: an event somebody chose with "다음 회의 잡기". ``rule``: the
day Follow-up's rule suggested, the one the proposal carries."""


@dataclass(frozen=True)
class DateChoice:
    day: date
    sources: tuple[Source, ...]
    """Why the day is offered; both when a picked day is also the rule's."""


def picked_days(result: Any) -> list[date]:
    """The days on C's ``next_meeting_days`` row, as ISO dates under ``days``.

    A failed read, or a row without the list, is no days: the card still
    offers the rule's. An entry that is not an ISO date is skipped.
    """
    if not getattr(result, "ok", False):
        return []
    days: list[date] = []
    for item in getattr(result, "items", []):
        entries = (getattr(item, "model_extra", None) or {}).get("days")
        if not isinstance(entries, list):
            continue
        for raw in entries:
            if not isinstance(raw, str):
                continue
            try:
                days.append(date.fromisoformat(raw))
            except ValueError:
                continue
    return days


def date_choices(rule_day: date | None, picked: Sequence[date]) -> list[DateChoice]:
    """Each picked day once, earliest first, then the rule's day unless it is
    one of them -- in which case that choice says both.

    The rule's day is last, so a day a person chose reads first. With nothing
    picked, the list is the rule's day alone; with no rule day either (a
    proposal from before #852), it is empty.
    """
    days = sorted(set(picked))
    choices = [
        DateChoice(day, ("picked", "rule") if day == rule_day else ("picked",)) for day in days
    ]
    if rule_day is not None and rule_day not in days:
        choices.append(DateChoice(rule_day, ("rule",)))
    return choices
