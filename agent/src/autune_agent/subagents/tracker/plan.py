"""Which stalled items to raise, and what to propose for each: the Tracker
subagent's rules, without a graph.

Deterministic on purpose, for Workload's reason: a proposal changes a person's
item on somebody else's approval, so what is proposed must be explainable in
one line and the same on every run over the same items. No model chooses it.

Items, never people. Nothing here counts, ranks or names who has left what
undone (#856): a proposal is about one item, and the summary counts proposals.

Confirmed items only. ``extraction.stalled_action_items`` also returns items
still waiting for a person's confirmation -- by id and days waited, without
their text -- and none of them becomes a proposal: an approval card for a
draft nobody has read would either hide what is approved or quote a model's
draft (agent-layer rule 3; mkkim68 on #856, 2026-10-07). Those stay with B's
morning DM and the review screen.

**One proposal for now: move a late item's due date.** #856 also accepts
closing an item carried through three or more meetings, and that is not here
yet. Closing can only mean marking the item done, and B tells a person what
they finished from the item's status alone -- it keeps that an item was edited
and never who edited it -- so an item the manager closed would be told to its
holder as work they finished. The user's decision (2026-10-07): B first learns
to mark "closed without finishing" and to say so apart from finished work;
the close proposal is added here once that is on main. Until then a
long-carried item that is not late gets no proposal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final

from autune_agent.results import Finding, ToolResult

MAX_PROPOSALS = 5
"""A subagent returns at most five items (agent-layer.md section 4), and the
approvals screen is not to fill with these (mkkim68 on #856)."""

MOVE_BY_DAYS = 7
"""How far ahead of the run's day a late item's new due date is put."""

MOVE: Final = "move_overdue_due_date"
"""The kind of the one proposal. Stored with the waiting row."""

CARRIED_WORDS = "오래 이월된 항목(회의 3번 이상)"
"""What a long-carried item is called to a person. Never "이월" alone: module
E's weekly report uses that word for every item confirmed before the period
and still open, which is a different count (lsh2217 on #856)."""


@dataclass(frozen=True)
class Stalled:
    """One confirmed, unfinished item ``stalled_action_items`` returned."""

    action_item_id: str
    title: str
    overdue: bool
    carried: bool


@dataclass(frozen=True)
class Move:
    """A late item and the date its due date would be moved to."""

    item: Stalled
    due_date: date

    @property
    def title(self) -> str:
        return f"기한 옮기기: {self.item.title}"

    @property
    def rationale(self) -> str:
        return f"기한이 지났습니다. 승인하면 기한이 {spoken(self.due_date)}로 바뀝니다."


def spoken(day: date) -> str:
    """A date as the approvals screen writes one: "10월 14일(수)"."""
    return f"{day.month}월 {day.day}일({'월화수목금토일'[day.weekday()]})"


def _extra(finding: Finding) -> dict[str, Any]:
    return finding.model_extra or {}


def stalled_from(result: ToolResult) -> list[Stalled]:
    """The confirmed items in the tool's order -- most pressing first.

    Left out: an item still waiting for confirmation (see the module
    docstring), an item the tool flags for reassignment -- its holder has left
    the team, and whoever handles that flag decides its date too -- and a row
    that says neither ``overdue`` nor ``carried``, which this subagent has no
    rule for.
    """
    found = []
    for row in result.items:
        extra = _extra(row)
        item_id = extra.get("id")
        ways = extra.get("stalled")
        if not isinstance(item_id, str) or not isinstance(ways, list):
            continue
        if "unconfirmed" in ways or extra.get("needs_reassignment"):
            continue
        overdue, carried = "overdue" in ways, "carried" in ways
        if not (overdue or carried):
            continue
        found.append(
            Stalled(action_item_id=item_id, title=row.title, overdue=overdue, carried=carried)
        )
    return found


def new_due_date(today: date) -> date:
    """``MOVE_BY_DAYS`` after ``today``, and the Monday after when that is a
    weekend. Public holidays are not looked at: the table of them is module
    B's, and no tool hands it out."""
    day = today + timedelta(days=MOVE_BY_DAYS)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def plan_moves(items: list[Stalled], *, today: date) -> list[Move]:
    """A move for each late item, ``MAX_PROPOSALS`` at most, in the tool's
    order -- late and long carried first. One proposal an item."""
    late = [item for item in items if item.overdue]
    return [Move(item=item, due_date=new_due_date(today)) for item in late[:MAX_PROPOSALS]]


def carried_only(items: list[Stalled]) -> int:
    """How many of ``items`` are long carried and not late: seen, and not yet
    proposed about (the module docstring)."""
    return sum(item.carried and not item.overdue for item in items)
