"""Who could take work from whom: the Workload subagent's rules, without a graph.

Deterministic on purpose. A reassignment moves a person's work, so what is
proposed must be explainable in one line and the same on every run over the
same counts; a model phrasing the proposal is welcome later, a model choosing
it is not. The manager approves each move one by one (agent-layer.md section
3.1), so these rules only have to propose sensibly, never decide.

Counts of work only. Nothing here reads or infers who spoke, how much, or on
what -- the tools this subagent may call return none of it (section 3.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from autune_agent.results import Finding, ToolResult

MAX_GIVERS = 3
"""The most loaded people considered per run; ``workload_by_owner`` shows three."""

MAX_PER_GIVER = 2
"""Moves taken from one person per run. Relief, not a reshuffle: the manager
sees a short list, and the next run proposes more if the load is still there."""

MAX_PER_TAKER = 2
"""Moves given to one person per run, so relief does not create the next pile."""

MAX_MOVES = 5
"""A subagent returns at most five items (section 4); one move is one item."""


@dataclass(frozen=True)
class Person:
    user_id: str
    name: str
    open: int
    overdue: int
    done: int
    state: str
    """``loaded``, ``free`` or empty, as ``extraction.workload_by_owner`` judged."""


@dataclass(frozen=True)
class Candidate:
    """One open item of a loaded person, as ``extraction.person_action_items`` ranks it."""

    action_item_id: str
    title: str
    score: float
    overdue: bool
    meeting_id: str | None = None
    """The item's own meeting, as the tool gave it. A proposal names it so
    that its card is that meeting's and not the one whose processing woke
    the run (#959)."""


@dataclass(frozen=True)
class Move:
    item: Candidate
    giver: Person
    taker: Person
    taker_open: int
    """The taker's open count before this move, counting earlier moves this run."""

    @property
    def title(self) -> str:
        return f"{self.item.title} → {self.taker.name}"

    @property
    def rationale(self) -> str:
        late = " · 기한 지남" if self.item.overdue else ""
        return (
            f"{self.giver.name}: 진행 중 {self.giver.open}건, 기한 지남 {self.giver.overdue}건 → "
            f"{self.taker.name}: 진행 중 {self.taker_open}건{late}"
        )


def _extra(finding: Finding) -> dict[str, Any]:
    return finding.model_extra or {}


def people_from(result: ToolResult) -> list[Person]:
    """The members in ``workload_by_owner``'s rows. The "담당 없음" row is not a
    person and cannot give or take, so it is left out here."""
    people = []
    for row in result.items:
        extra = _extra(row)
        user_id = extra.get("id")
        if not isinstance(user_id, str) or user_id == "unowned":
            continue
        people.append(
            Person(
                user_id=user_id,
                name=row.title,
                open=int(extra.get("open", 0)),
                overdue=int(extra.get("overdue", 0)),
                done=int(extra.get("done", 0)),
                state=str(extra.get("state", "")),
            )
        )
    return people


def candidates_from(result: ToolResult) -> list[Candidate]:
    """The person's items in the tool's order -- most urgent first. An item the
    tool already flags for reassignment is left to whoever handles that flag."""
    found = []
    for row in result.items:
        extra = _extra(row)
        item_id = extra.get("id")
        if not isinstance(item_id, str) or extra.get("needs_reassignment"):
            continue
        meeting_id = extra.get("meeting_id")
        found.append(
            Candidate(
                action_item_id=item_id,
                title=row.title,
                score=row.score,
                overdue=bool(extra.get("overdue")),
                meeting_id=meeting_id if isinstance(meeting_id, str) else None,
            )
        )
    return found


def givers(people: list[Person]) -> list[Person]:
    return [p for p in people if p.state == "loaded"][:MAX_GIVERS]


def takers(people: list[Person]) -> list[Person]:
    """Who could take work: people with nothing open first, as the tool ranked
    them (most finished first); then anyone below the team's mean with nothing
    overdue, fewest open first. Nobody loaded, and nobody running late."""
    free = [p for p in people if p.state == "free"]
    mean = _mean_open(people)
    light = sorted(
        (p for p in people if p.state == "" and p.overdue == 0 and p.open < mean and p not in free),
        key=lambda p: p.open,
    )
    return free + light


def _mean_open(people: list[Person]) -> float:
    return sum(p.open for p in people) / len(people) if people else 0.0


def plan_moves(people: list[Person], items_of: dict[str, list[Candidate]]) -> list[Move]:
    """Moves from each loaded person to the lightest taker, most urgent item first.

    From one person: enough to bring them to the team's mean, at least one and
    at most ``MAX_PER_GIVER``. A move never leaves the taker holding as much as
    the giver still does -- that would only move the pile.
    """
    open_now = {p.user_id: p.open for p in people}
    given: dict[str, int] = {}
    target = ceil(_mean_open(people))
    moves: list[Move] = []
    candidates = takers(people)
    for giver in givers(people):
        wanted = min(MAX_PER_GIVER, max(1, giver.open - target))
        for item in items_of.get(giver.user_id, [])[:wanted]:
            if len(moves) == MAX_MOVES:
                return moves
            eligible = [
                t
                for t in candidates
                if given.get(t.user_id, 0) < MAX_PER_TAKER
                and open_now[t.user_id] + 1 < open_now[giver.user_id]
            ]
            if not eligible:
                break
            taker = min(eligible, key=lambda t: (open_now[t.user_id], candidates.index(t)))
            moves.append(
                Move(item=item, giver=giver, taker=taker, taker_open=open_now[taker.user_id])
            )
            given[taker.user_id] = given.get(taker.user_id, 0) + 1
            open_now[taker.user_id] += 1
            open_now[giver.user_id] -= 1
    return moves
