"""When another meeting looks needed (spec section 4).

Pure functions over tool results, no model call. Like Workload's ``plan.py``,
the decision is a rule the team can read and test, and the lead approves what
it proposes, so the rule only has to propose sensibly, never decide.

Both thresholds are first guesses, to be checked on W5's real meetings (#22).
So are the suggested date's share and horizon (#963).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median_low
from typing import Literal

from autune_agent.results import Finding, ToolResult

MIN_HIGH_GAPS = 2
"""Open high-severity gaps that, with an unresolved question, leave a meeting heavy."""

MAX_EVIDENCE = 5
"""Gap ids a proposal carries: the ones the lead's preview shows (#562)."""

DEFAULT_BUSINESS_DAYS = 3
"""How far ahead a follow-up is suggested when the team's rhythm is unknown."""

MAX_CADENCE_DAYS = 14
"""The longest gap between meetings a suggestion follows: a follow-up is about
what was left open, so it should not wait for a team that meets monthly."""

DUE_DATE_SHARE = 0.8
"""The share of M's work that should be due before the follow-up (#963). A
first guess, checked on W5's real meetings (#22)."""

DUE_HORIZON_DAYS = 14
"""Items due later than this many days from today are long-running work, left
to a later meeting rather than this follow-up (#963). A first guess (#22)."""

Basis = Literal["confirmed", "draft", "cadence"]
"""What a suggested date rests on: confirmed due dates only, due dates of which
at least one is still a draft, or the team's meeting rhythm. The approvals card
marks a ``draft`` date "초안 기준" (#963)."""

Kind = Literal["followup_reopened", "followup_risky", "followup_reopened_risky"]
"""Which of section 4's rules fired, as the proposal's ``kind`` (#854). The
pending row keeps ``kind`` and no rationale (agent/CLAUDE.md rule 8), so the
approvals card turns the code into its one line of why."""


@dataclass(frozen=True)
class Verdict:
    carried: tuple[str, ...]
    """M's gap ids on a template item the previous analysed meeting left open too."""
    heavy: tuple[str, ...]
    """M's open high-severity gap ids, when there are enough of them and a question."""

    @property
    def fires(self) -> bool:
        return bool(self.carried or self.heavy)

    @property
    def evidence(self) -> list[str]:
        """Carried-over gaps first, then heavy ones, each once."""
        return list(dict.fromkeys(self.carried + self.heavy))[:MAX_EVIDENCE]

    @property
    def kind(self) -> Kind:
        if self.carried and self.heavy:
            return "followup_reopened_risky"
        return "followup_reopened" if self.carried else "followup_risky"

    def reason(self) -> str:
        parts = []
        if self.carried:
            parts.append(f"직전 회의에 이어 다시 열린 항목 {len(self.carried)}개")
        if self.heavy:
            parts.append(f"높음 갭 {len(self.heavy)}건과 미해결 질문")
        return ", ".join(parts)


def _id(item: Finding) -> str | None:
    value = getattr(item, "id", None)
    return value if isinstance(value, str) else None


def decide(open_gaps: ToolResult, recurring: ToolResult, questions: ToolResult) -> Verdict:
    """Either rule of section 4, over C's two reads and B's questions."""
    carried = tuple(i for i in map(_id, recurring.items) if i)
    high = tuple(
        i
        for item in open_gaps.items
        if getattr(item, "severity", None) == "high" and (i := _id(item))
    )
    heavy = high if len(high) >= MIN_HIGH_GAPS and questions.items else ()
    return Verdict(carried=carried, heavy=heavy)


def cited(open_gaps: ToolResult, verdict: Verdict) -> list[Finding]:
    """The gaps a proposal rests on, in its evidence's order, for the chat answer."""
    by_id = {i: item for item in open_gaps.items if (i := _id(item))}
    return [by_id[i] for i in verdict.evidence if i in by_id]


def is_business_day(day: date, off: AbstractSet[date] = frozenset()) -> bool:
    """A weekday that is not one of ``off``, the public holidays (#964)."""
    return day.weekday() < 5 and day not in off


def _business_days_after(day: date, n: int, off: AbstractSet[date] = frozenset()) -> date:
    while n > 0:
        day += timedelta(days=1)
        if is_business_day(day, off):
            n -= 1
    return day


def suggest_date(held: list[date], today: date, off: AbstractSet[date] = frozenset()) -> date:
    """When the follow-up meeting could be: the team's usual gap after its
    latest meeting, never before the next business day.

    ``held`` is the days the team's past meetings started on, in any order.
    The usual gap is the median of the gaps between them, kept to one to
    ``MAX_CADENCE_DAYS`` days; with fewer than two days it is unknown and the
    suggestion is ``DEFAULT_BUSINESS_DAYS`` business days from today. A
    weekend or a public holiday in ``off`` moves to the next business day.
    Without ``off`` only weekends are skipped, and the lead moves the date on
    the board.

    Meeting days only: no calendar and nobody's availability (spec section 6).
    """
    days = sorted(set(held), reverse=True)
    earliest = _business_days_after(today, 1, off)
    if len(days) < 2:
        return _business_days_after(today, DEFAULT_BUSINESS_DAYS, off)
    gaps = [(a - b).days for a, b in zip(days, days[1:], strict=False)]
    cadence = min(max(median_low(gaps), 1), MAX_CADENCE_DAYS)
    day = max(days[0] + timedelta(days=cadence), earliest)
    while not is_business_day(day, off):
        day += timedelta(days=1)
    return day


@dataclass(frozen=True)
class Due:
    """One open, dated action item of M: its due date and whether a person
    confirmed it. Nothing else -- no title, no assignee (spec section 6)."""

    day: date
    confirmed: bool


@dataclass(frozen=True)
class Suggestion:
    day: date
    basis: Basis


def suggest_from_due_dates(
    due: Sequence[Due], today: date, off: AbstractSet[date] = frozenset()
) -> Suggestion | None:
    """The follow-up just after most of M's work is due (spec section 5, #963).

    In order: a confirmed item already past its date makes it the next
    business day. A draft's past date is dropped instead -- a date B misread is
    more likely than work late minutes after the meeting. Items due after
    ``DUE_HORIZON_DAYS`` are dropped too. Of what is left, the date is the
    next business day after the ``DUE_DATE_SHARE`` point, never before the
    next business day after today.

    ``None`` when no date is left: the caller falls back to ``suggest_date``.
    The same items in any order give the same suggestion. A business day is
    a weekday not in ``off``, the public holidays (#964).
    """
    earliest = _business_days_after(today, 1, off)
    if any(d.confirmed and d.day < today for d in due):
        return Suggestion(earliest, "confirmed")
    horizon = today + timedelta(days=DUE_HORIZON_DAYS)
    kept = sorted((d for d in due if today <= d.day <= horizon), key=lambda d: d.day)
    if not kept:
        return None
    # Rounded first: 0.8 * 15 is 12.000000000000002 in floating point.
    k = max(math.ceil(round(DUE_DATE_SHARE * len(kept), 9)), 1)
    day = max(_business_days_after(kept[k - 1].day, 1, off), earliest)
    basis: Basis = "confirmed" if all(d.confirmed for d in kept) else "draft"
    return Suggestion(day, basis)
