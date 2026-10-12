"""A stored proposal's reason sentence, filled when the approvals card is shown.

#1037, decided by mkkim68 on 2026-10-08: a pending row keeps the reason's
template only (``explain.Reason.template``: blanks, no date, no title), and the
card fills it when it is shown. A filled sentence holds an item's title, so it
is stored nowhere (#1062). These two functions are what ``main/`` calls, and
their signatures are kept:

- ``storable(template)`` before the row is written: a template that fails it is
  not kept.
- ``card_reason(...)`` when the card is shown.

Pure: the caller hands in the row's values and what it read through B's tools
(and A's, for a date set by the team's rhythm). No tool call, no model,
nothing stored.
"""

from __future__ import annotations

from datetime import date

from autune_agent.results import ToolResult

from . import explain, rules
from .graph import _due, _held, _holidays

storable = explain.storable


def card_reason(
    template: str,
    day: date,
    basis: str,
    made_on: date,
    due_dates: ToolResult,
    holidays: ToolResult,
    recent: ToolResult | None = None,
) -> str | None:
    """The stored template filled for the card, or ``None`` when there is
    nothing true to say.

    ``day`` and ``basis`` are the row's arguments; ``made_on`` is the KST day the
    proposal was made, from the row's creation time. The rule runs again on
    ``made_on`` with what B holds now -- ``meeting_due_dates`` and
    ``public_holidays`` -- and, when no due date gives a day, A's
    ``recent_meetings`` (``recent``; without it a rhythm-set date gets no
    sentence). The blanks are filled from that run, so dates and the title come
    from the rule, as at proposal time.

    ``None`` when:

    - the template is not ``storable``;
    - the run gives another day or basis -- B's due dates changed since, say --
      so the sentence would explain a date the card does not show. Meetings
      held after ``made_on`` are not read, so they change nothing;
    - the template does not pass ``explain.usable`` against that run.

    The card then shows the date without a sentence.
    """
    if not explain.storable(template):
        return None
    off = _holidays(holidays)
    suggestion = rules.suggest_from_due_dates(_due(due_dates), made_on, off)
    if suggestion is None and recent is not None:
        held = [d for d in _held(recent) if d <= made_on]
        suggestion = rules.suggest_by_rhythm(held, made_on, off)
    if suggestion is None or suggestion.day != day or suggestion.basis != basis:
        return None
    if not explain.usable(template, suggestion):
        return None
    return explain.fill(template, suggestion)


__all__ = ["card_reason", "storable"]
