"""The approvals card's hint: the date Follow-up would suggest now (spec section 7).

The card shows the stored date, which is what an approval runs, and beside it
the date the same rule gives from the due dates confirmed by now (#972, #967).
This is that second date, for ``main/preview.py`` to call -- the one function
of this package the card depends on, so its signature is kept (#967, option 2).

Pure: the caller hands in what it already read through B's tools and today's
date. No tool call, no clock, no model, nothing stored.
"""

from __future__ import annotations

from datetime import date

from autune_agent.results import ToolResult

from . import rules
from .graph import _due, _holidays


def card_hint(due_dates: ToolResult, holidays: ToolResult, today: date) -> rules.Suggestion | None:
    """The date the rule gives now, from B's ``meeting_due_dates`` and
    ``public_holidays`` results, or ``None`` when there is nothing to say.

    ``None`` when the due-date read failed or holds no usable date: the card
    then shows the stored date alone. The team's rhythm is not consulted -- a
    hint is about work confirmed since the proposal, and the stored date
    already is the rhythm's when there was none. A failed holiday read counts
    weekends only, as a proposal does.

    The same inputs give the date a proposal made with them would carry
    (``rules.suggest_from_due_dates``), so the card never sets two rules side
    by side.
    """
    return rules.suggest_from_due_dates(_due(due_dates), today, _holidays(holidays))
