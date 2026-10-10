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
from dataclasses import dataclass, field
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
    """The day ``suggest_by_rhythm`` gives."""
    return suggest_by_rhythm(held, today, off).day


def suggest_by_rhythm(
    held: list[date], today: date, off: AbstractSet[date] = frozenset()
) -> Suggestion:
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
        day = _business_days_after(today, DEFAULT_BUSINESS_DAYS, off)
        return Suggestion(day, "cadence", Why(step="default", meetings=len(days)))
    gaps = [(a - b).days for a, b in zip(days, days[1:], strict=False)]
    cadence = min(max(median_low(gaps), 1), MAX_CADENCE_DAYS)
    planned = days[0] + timedelta(days=cadence)
    day = max(planned, earliest)
    while not is_business_day(day, off):
        day += timedelta(days=1)
    why = Why(
        step="cadence",
        meetings=len(days),
        last_meeting=days[0],
        cadence_days=cadence,
        held_to_earliest=planned < earliest,
        moved_off_day=day != max(planned, earliest),
        planned=max(planned, earliest),
        skipped=_skipped(max(planned, earliest), day),
    )
    return Suggestion(day, "cadence", why)


def _skipped(start: date, day: date) -> tuple[date, ...]:
    """The days from ``start`` up to ``day`` that were passed over as days off."""
    return tuple(start + timedelta(days=n) for n in range((day - start).days))


@dataclass(frozen=True)
class Due:
    """One open, dated action item of M: its due date, whether a person
    confirmed it and, for a confirmed one, its title when B hands it over.
    Never an assignee (spec section 6)."""

    day: date
    confirmed: bool
    title: str | None = field(default=None, compare=False)
    """For the reason sentence only; the date rule never reads it."""


Step = Literal["overdue", "due_share", "cadence", "default"]
"""Which step of the date rule gave the day (spec section 5): a confirmed item
already late (2), the ``DUE_DATE_SHARE`` point of M's due dates (4), the
team's rhythm (5), or ``DEFAULT_BUSINESS_DAYS`` when the rhythm is unknown."""


@dataclass(frozen=True)
class Why:
    """What the rule used to reach its day, as values (spec section 7, Stage 2).

    The reason sentence is written from these and nothing else, so it can say
    no more than the rule did. Dates, counts, flags and at most one confirmed
    item's title; no owner and no other meeting text. Only the counts reach a
    model (``explain.facts``); dates and the title fill its blanks in code.
    """

    step: Step
    overdue: int = 0
    """Confirmed items already past their due date (step 2)."""
    used: tuple[date, ...] = ()
    """The due dates step 4 counted, ascending."""
    covered: int = 0
    """How many of ``used`` are due by ``share_point``."""
    share_point: date | None = None
    """The due date at the ``DUE_DATE_SHARE`` point; the day is the business day after."""
    beyond_horizon: int = 0
    """Due dates dropped as past ``DUE_HORIZON_DAYS``."""
    past_drafts: int = 0
    """Unconfirmed due dates dropped as already past."""
    meetings: int = 0
    """The team's meeting days the rhythm read (steps 5 and default)."""
    last_meeting: date | None = None
    cadence_days: int | None = None
    """The team's usual gap between meetings, in days."""
    held_to_earliest: bool = False
    """The rule's day was before the next business day, so that day was taken."""
    moved_off_day: bool = False
    """The day fell on a weekend or a public holiday and moved past it."""
    planned: date | None = None
    """The day before any move off a weekend or holiday (steps 4 and 5)."""
    skipped: tuple[date, ...] = ()
    """The days off passed over between ``planned`` and the day."""
    title: str | None = None
    """Step 4: a title of an item due by ``share_point``, the one due last of
    those with a title. ``None`` when B handed over none."""


@dataclass(frozen=True)
class Suggestion:
    day: date
    basis: Basis
    why: Why | None = field(default=None, compare=False)
    """What the rule used. Not compared: two suggestions are the same date on
    the same basis."""


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
    overdue = sum(1 for d in due if d.confirmed and d.day < today)
    if overdue:
        return Suggestion(earliest, "confirmed", Why(step="overdue", overdue=overdue))
    horizon = today + timedelta(days=DUE_HORIZON_DAYS)
    kept = sorted(
        (d for d in due if today <= d.day <= horizon), key=lambda d: (d.day, d.title or "")
    )
    if not kept:
        return None
    # Rounded first: 0.8 * 15 is 12.000000000000002 in floating point.
    k = max(math.ceil(round(DUE_DATE_SHARE * len(kept), 9)), 1)
    point = kept[k - 1].day
    after = _business_days_after(point, 1, off)
    day = max(after, earliest)
    basis: Basis = "confirmed" if all(d.confirmed for d in kept) else "draft"
    by_point = [d for d in kept if d.day <= point]
    titled = [d.title for d in by_point if d.confirmed and d.title]
    why = Why(
        step="due_share",
        used=tuple(d.day for d in kept),
        covered=len(by_point),
        share_point=point,
        beyond_horizon=sum(1 for d in due if d.day > horizon),
        past_drafts=sum(1 for d in due if not d.confirmed and d.day < today),
        held_to_earliest=after < earliest,
        moved_off_day=day == after and after != point + timedelta(days=1),
        planned=point + timedelta(days=1),
        skipped=_skipped(point + timedelta(days=1), day) if day == after else (),
        title=titled[-1] if titled else None,
    )
    return Suggestion(day, basis, why)
