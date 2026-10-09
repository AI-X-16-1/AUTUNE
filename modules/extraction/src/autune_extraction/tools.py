"""Module B as tools an agent can call (#260/#261, docs/architecture/agent-layer.md section 4).

Nine read tools (``TOOLS``) over B's existing reads, and seven writes
(``ACTIONS``) over B's existing service calls, so that everything a person does
with B on the board -- read items and decisions, confirm, reassign, re-date,
close, add, review a decision -- can also be asked for in words. No new tables,
no contract change. Each returns a dict in the shape agent-layer.md calls
``ToolResult``::

    {"ok", "reason", "summary", "items", "evidence", "confidence", "truncated"}

**Plain functions, no decorator, no ``autune_agent`` import.** ADR 0010's fourth
import-linter contract puts ``autune_agent`` above every module, so a module that
imported ``autune_agent.tools`` (as section 4's example does) would fail
``lint-imports``. Raised on #261 with this shape as the proposal: the agent wraps
``TOOLS`` when it collects them and validates the dict into its ``ToolResult``;
until then these are callable and tested on their own.

Rules from section 4 that are enforced here rather than trusted to the caller:

- ``items`` holds at most ``MAX_ITEMS``; ``truncated`` says when more existed.
- ``evidence`` holds utterance ids only, never text.
- An expected failure (unknown meeting) is ``ok=False`` with a reason, not an
  exception.
- Synchronous, safe to call twice: every tool only reads. The writes are
  ``ACTIONS``, never in ``TOOLS``: level L2, run by the main agent only after a
  person approves (section 8) -- except ``L1_ACTIONS``, a draft that waits on
  the board instead, each through the same service call and checks
  the board uses. ``RUN_SCOPE`` names the arguments the run fills, not the model.

**Rule 3 of #261 -- B content that leaves goes through B's outbound read.** An
item's text appears in ``items`` only once a person has confirmed it (the same
gate as ``service.outbound_for_meeting``). What is still unconfirmed is counted,
not quoted. The one exception is ``unresolved_questions``, whose whole point is
utterances nobody has confirmed; its docstring says the agent may use them
internally (L0) and must not post them anywhere without a person's review.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus, UtteranceKind
from autune_core import Meeting, TeamMember, User, Utterance, session_scope
from autune_integrations.privacy import find_unmasked

from . import days_off, service, tasks
from .models import ExtActionItem, ExtDecision, ExtProject
from .pipeline.base import give_roster
from .pipeline.registry import get_resolver
from .schemas import ActionItemCreate, ActionItemRead, ActionItemUpdate, DecisionReviewUpdate
from .slots import KST

MAX_ITEMS = 5
"""agent-layer.md section 4: a tool ranks and keeps five; the rest stay in B's tables."""

MAX_DAYS = 365
"""The longest window a tool takes, in days. A year covers every meeting inside
any retention window a team can set."""

_OPEN = (ActionStatus.TODO, ActionStatus.IN_PROGRESS)


def _today() -> date:
    """Today, as every tool here means it: the date in Korea, not the server's.

    There is no team time zone. A server's ``date.today()`` on UTC is a day
    behind from 00:00 to 09:00 KST, and in those hours an item due today was
    answered as due tomorrow and an item a day late as not late -- against the
    reminders and ``service.team_action_progress`` (#619 review), which take
    Korea's day.
    """
    return datetime.now(tz=KST).date()


def _result(
    *,
    summary: str,
    items: list[dict[str, Any]],
    evidence: list[str],
    confidence: float = 1.0,
    ok: bool = True,
    reason: str | None = None,
) -> dict[str, Any]:
    kept = items[:MAX_ITEMS]
    return {
        "ok": ok,
        "reason": reason,
        "summary": summary,
        "items": kept,
        # Ids only, and only for what was kept, so evidence never outgrows items.
        "evidence": list(dict.fromkeys(evidence)),
        "confidence": confidence,
        "truncated": len(items) > MAX_ITEMS,
    }


def _whole_days(value: object, *, low: int) -> int | None:
    """A day count a model wrote, as an integer inside ``low..MAX_DAYS`` -- or
    ``None`` when it is not a count at all.

    The ask loop lets a model write a read tool's arguments (#677), and
    ``timedelta(days=...)`` raises on a number it cannot hold and on anything
    that is not a number; that exception would be the tool's answer. A count
    outside the range is pulled into it: ten years of meetings is all of them.
    A value that is not a whole number -- text, a fraction, a boolean, nan --
    is refused, since guessing what it meant would answer a different question.
    ``7.0`` is 7: JSON has one number type.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float):
        if not value.is_integer():  # nan and the infinities are not
            return None
        value = int(value)
    return max(low, min(value, MAX_DAYS))


def _not_a_day_count(name: str) -> dict[str, Any]:
    """The refusal for an argument that is not a day count. It names the
    argument and never repeats the value: that is whatever the model wrote,
    it can carry text from the person's question, and ``reason`` goes back to
    the model and into logs (the rule ``registry.bind_scope`` keeps)."""
    return _result(
        ok=False,
        reason=f"{name} is not a whole number of days",
        summary="기간은 일 단위의 정수여야 합니다.",
        items=[],
        evidence=[],
        confidence=0.0,
    )


def _missing(meeting_id: str) -> dict[str, Any]:
    return _result(
        ok=False,
        reason=f"no meeting {meeting_id}",
        summary="회의를 찾을 수 없습니다.",
        items=[],
        evidence=[],
        confidence=0.0,
    )


def _item_finding(item: ActionItemRead, today: date) -> dict[str, Any]:
    who = item.assignee_name or item.assignee_label or "담당 미지정"
    if item.needs_reassignment:
        who = "재배정 필요"
    due = item.due_date.isoformat() if item.due_date else "기한 없음"
    overdue = _overdue(item, today)
    # ``done`` would have the reader say it was finished (review of #979).
    status = "closed" if item.closed_unfinished else item.status
    return {
        "title": item.description,
        "body": f"{who} · {due}{' · 기한 지남' if overdue else ''} · {status}",
        "score": _urgency(item, today),
        "id": item.id,
        "meeting_id": item.meeting_id,
        "overdue": overdue,
        "needs_reassignment": item.needs_reassignment,
    }


def _overdue(item: ActionItemRead, today: date) -> bool:
    """Past its date and not done -- a finished item is never late."""
    return (
        item.status != ActionStatus.DONE.value
        and item.due_date is not None
        and item.due_date < today
    )


def _urgency(item: ActionItemRead, today: date) -> float:
    """Reassignment first, then overdue, then due soonest, undated after that,
    done last -- the order a person scanning a board would want the five in."""
    if item.status == ActionStatus.DONE.value:
        return 0.0
    if item.needs_reassignment:
        return 1.0
    if item.due_date is None:
        return 0.1
    days = (item.due_date - today).days
    if days < 0:
        return 0.9
    return max(0.2, 0.8 - 0.05 * days)


def meeting_action_items(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this right after a meeting is processed, or when asked what a meeting
    decided people should do. Do not use it for work across several meetings --
    that is ``open_action_items`` -- or for what still needs a person's review --
    that is ``review_state``.

    Returns the meeting's confirmed action items, most urgent first (at most
    five), and in ``summary`` how many are still waiting for confirmation.
    Unconfirmed items are counted, never quoted. Each item's line ends with its
    status; ``closed`` is an item closed without being finished -- do not
    report it as done.
    """
    if service.live_meeting(session, meeting_id) is None:
        return _missing(meeting_id)
    today = _today()
    confirmed = service.outbound_for_meeting(session, meeting_id).action_items
    waiting = [
        i
        for i in service.list_action_items(session, meeting_id=meeting_id)
        if i.status == ActionStatus.NEEDS_CONFIRMATION.value
    ]
    ranked = sorted(confirmed, key=lambda i: _urgency(i, today), reverse=True)
    reassign = sum(i.needs_reassignment for i in confirmed)
    summary = f"확정된 액션아이템 {len(confirmed)}건, 확인 필요 {len(waiting)}건."
    if reassign:
        summary += f" 담당자가 팀에 없어 재배정이 필요한 항목 {reassign}건."
    return _result(
        summary=summary,
        items=[_item_finding(i, today) for i in ranked],
        evidence=[u for i in ranked[:MAX_ITEMS] for u in i.source_utterance_ids],
    )


def _shown_title(description: str) -> dict[str, str]:
    """A due-date entry's ``title``, or nothing (#1038): the item's description
    when it carries no personal data. A description a person typed or edited
    never passed module A's masker, so it is screened here as
    ``service.outbound_for_meeting`` screens what leaves for Notion, Jira or
    Slack; one that fails gives the entry no ``title`` key and stays in B."""
    return {} if find_unmasked(description) else {"title": description}


def meeting_due_dates(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this when a day has to be chosen around one meeting's work -- a
    follow-up meeting after most of what it agreed is due (#963, #966). Do not
    use it to learn what the items are, whose they are or which is late: that
    is ``meeting_action_items``.

    Returns one row, ``기한``, whose ``due_dates`` lists the due date of every
    action item of the meeting that is **confirmed**, not done and has a date,
    earliest first -- one entry an item, so a day two items share is there
    twice. An entry is a date, ``confirmed`` and, when it may be shown, the
    item's ``title`` (below) -- never an assignee. With an assignee beside it a
    date would say who is late; without one it says only when the meeting's
    work falls due and what a piece of it is called. ``evidence`` holds those
    items' ids.

    **``title`` is a confirmed item's description, for one use: a sentence shown
    to the meeting's own team that names a piece of the work beside the day**
    (#1038; the user, 2026-10-08: "두 조건을 붙여 추가"). The two conditions:

    - *Screened.* An entry has a ``title`` only when ``find_unmasked`` finds no
      personal data in the description (``_shown_title``). Otherwise the entry
      is a date and ``confirmed`` as before: the item still counts and its text
      stays in B. A caller has to work without a title.
    - *Shown, not kept.* The title fills a sentence at the moment it is shown.
      It is not to be written into an ``agent_`` row or any other store outside
      B: a copy there would outlive the item's deletion and the meeting's
      retention, which B's own row does not. A card that shows the sentence
      later reads this tool again and fills it then. B cannot enforce this on a
      caller; it is the condition the title is handed over on.

    Every confirmed entry's title comes out, not one: which item a sentence
    names is the caller's rule. A caller that puts a title into text a model
    then reads -- the main agent's compose step in a chat turn does -- sends it
    to that model, through ``check_outbound``, as it already does a title from
    ``meeting_action_items``.

    The row also counts, for a card that says how much of the dated work is
    settled ("기한 있는 항목 0/5 확정", #966, #967): ``dated_open`` is how many
    items of the meeting are not done and have a date, confirmed or not, and
    ``dated_confirmed`` how many of those are confirmed -- the length of
    ``due_dates``. So the row is there whenever any unfinished item has a
    date, with an empty ``due_dates`` when none of them is confirmed, and
    there is no row at all when none has one.

    **An unconfirmed item's date does not come out** (#246, #261 rule 3: B's
    unconfirmed content is counted, never quoted; the user, 2026-10-07, asked
    whether a draft's date might go: "확정된 항목의 기한만"). A date is not
    text, but it is content of a draft nobody has accepted, and a date
    recommended to a team from it would be built on that draft. Unconfirmed
    items are in ``summary`` as a count, with how many confirmed open items
    there are and how many of those have no date, and in ``dated_open`` as a
    count of the dated ones (decided on #966, 2026-10-07: a number is still
    "counted, never quoted") -- never as a date, an id or an entry of
    ``evidence``. ``confirmed`` is therefore
    always ``true``; it is in each entry because the caller asked for the
    shape, so nothing has to change there if a draft's date is ever allowed.

    A meeting that does not exist or is past retention is ``ok: false``, as
    for every tool that takes one.
    """
    if service.live_meeting(session, meeting_id) is None:
        return _missing(meeting_id)
    rows = service.list_action_items(session, meeting_id=meeting_id)
    waiting = [i for i in rows if i.status == ActionStatus.NEEDS_CONFIRMATION.value]
    opened = [i for i in rows if i.status in {status.value for status in _OPEN}]
    dated = sorted((i for i in opened if i.due_date is not None), key=lambda i: (i.due_date, i.id))
    # Counted and nothing more: which drafts, and their dates, stay in B.
    dated_open = len(dated) + sum(i.due_date is not None for i in waiting)
    summary = (
        f"확정된 열린 액션아이템 {len(opened)}건 중 기한 있음 {len(dated)}건, "
        f"기한 없음 {len(opened) - len(dated)}건. 확인 필요 {len(waiting)}건."
    )
    items = (
        [
            {
                "title": "기한",
                # One row holding them all: a row an item would be cut at five
                # (``MAX_ITEMS``), and the rule this feeds needs every date.
                "due_dates": [
                    {
                        "date": i.due_date.isoformat(),
                        "confirmed": True,
                        **_shown_title(i.description),
                    }
                    for i in dated
                    if i.due_date is not None
                ],
                "dated_open": dated_open,
                "dated_confirmed": len(dated),
            }
        ]
        if dated_open
        else []
    )
    return _result(summary=summary, items=items, evidence=[i.id for i in dated])


def open_action_items(session: Session, team_id: str, *, within_days: int = 7) -> dict[str, Any]:
    """Use this for a morning briefing or when asked what is late, due soon, or
    left without an owner across a team's meetings. Do not use it for one
    meeting's outcome -- that is ``meeting_action_items``.

    Returns the team's open confirmed items (to do or in progress) that are
    overdue, due within ``within_days``, or need reassigning -- most urgent first,
    at most five -- with counts of each group in ``summary``. ``within_days`` is
    a whole number from 0 to 365.
    """
    window = _whole_days(within_days, low=0)
    if window is None:
        return _not_a_day_count("within_days")
    today = _today()
    horizon = today + timedelta(days=window)
    meeting_ids = set(
        session.scalars(
            select(Meeting.id).where(Meeting.team_id == team_id, service.within_retention())
        )
    )
    if not meeting_ids:
        return _result(summary="이 팀의 회의가 없습니다.", items=[], evidence=[])
    open_items = [
        i
        for status in _OPEN
        for i in service.list_action_items(session, status=status)
        if i.meeting_id in meeting_ids
    ]
    due = [
        i
        for i in open_items
        if i.needs_reassignment or (i.due_date is not None and i.due_date <= horizon)
    ]
    ranked = sorted(due, key=lambda i: _urgency(i, today), reverse=True)
    overdue = sum(_overdue(i, today) for i in due)
    soon = sum(1 for i in due if i.due_date is not None and today <= i.due_date <= horizon)
    reassign = sum(i.needs_reassignment for i in due)
    return _result(
        summary=(
            f"진행 중인 액션아이템 {len(open_items)}건 중 기한 지남 {overdue}건, "
            f"{window}일 안에 기한 {soon}건, 재배정 필요 {reassign}건."
        ),
        items=[_item_finding(i, today) for i in ranked],
        evidence=[u for i in ranked[:MAX_ITEMS] for u in i.source_utterance_ids],
    )


STALLED_UNCONFIRMED_DAYS = 3
"""How long an item may wait for confirmation before ``stalled_action_items``
counts it, unless the caller says otherwise (the user, 2026-10-05, #856)."""


def stalled_action_items(
    session: Session, team_id: str, *, unconfirmed_days: int = STALLED_UNCONFIRMED_DAYS
) -> dict[str, Any]:
    """Use this when deciding which of a team's action items somebody should
    be asked to look at again -- work that has stopped moving. Do not use it
    for what is due soon -- that is ``open_action_items`` -- or for one
    meeting's review -- that is ``review_state``.

    Returns the team's items that are stalled in one of three ways, most
    pressing first (at most five), each saying which in ``stalled``, with a
    count of each in ``summary``:

    - ``overdue`` -- confirmed, unfinished, and past its due date;
    - ``carried`` -- confirmed, unfinished, and carried through
      ``service.STALE_AFTER`` or more of the team's later meetings (the board's
      own mark, #794);
    - ``unconfirmed`` -- still waiting for a person's confirmation
      ``unconfirmed_days`` or more after it was made (a whole number from 1 to
      365; 3 unless given).

    An unconfirmed item is given by id, meeting and how long it has waited --
    never its text, its assignee or its date: nothing a model drafted is
    quoted before a person has confirmed it (#261 rule 3). It is about items,
    not people: nothing here counts or ranks what a person has left undone.
    """
    window = _whole_days(unconfirmed_days, low=1)
    if window is None:
        return _not_a_day_count("unconfirmed_days")
    meeting_ids = set(
        session.scalars(
            select(Meeting.id).where(Meeting.team_id == team_id, service.within_retention())
        )
    )
    if not meeting_ids:
        return _result(summary="이 팀의 회의가 없습니다.", items=[], evidence=[])

    today = _today()
    open_items = [
        i
        for status in _OPEN
        for i in service.list_action_items(session, status=status)
        if i.meeting_id in meeting_ids
    ]
    confirmed: list[tuple[ActionItemRead, list[str]]] = []
    for i in open_items:
        ways = [
            way
            for way, is_so in (
                ("overdue", _overdue(i, today)),
                ("carried", i.carried_meetings >= service.STALE_AFTER),
            )
            if is_so
        ]
        if ways:
            confirmed.append((i, ways))
    # Both ways before one; late before carried; then the longer carried.
    confirmed.sort(key=lambda pair: (len(pair[1]), "overdue" in pair[1], pair[0].carried_meetings))
    confirmed.reverse()

    now = datetime.now(tz=UTC)
    waiting: list[tuple[int, str, str]] = []
    for item_id, meeting_id, made in session.execute(
        select(ExtActionItem.id, ExtActionItem.meeting_id, ExtActionItem.created_at).where(
            ExtActionItem.meeting_id.in_(meeting_ids),
            ExtActionItem.status == ActionStatus.NEEDS_CONFIRMATION.value,
        )
    ).tuples():
        days = (now - (made if made.tzinfo else made.replace(tzinfo=UTC))).days
        if days >= window:
            waiting.append((days, item_id, meeting_id))
    waiting.sort(reverse=True)

    items: list[dict[str, Any]] = [
        {
            **_item_finding(i, today),
            "score": 0.9 if "overdue" in ways else 0.7,
            "stalled": ways,
            "carried_meetings": i.carried_meetings,
        }
        for i, ways in confirmed
    ]
    items += [
        {
            "title": "액션아이템 확인 필요",
            "body": f"{days}일째 확인 필요",
            "score": 0.5,
            "id": item_id,
            "meeting_id": meeting_id,
            "stalled": ["unconfirmed"],
            "waiting_days": days,
        }
        for days, item_id, meeting_id in waiting
    ]
    overdue = sum("overdue" in ways for _, ways in confirmed)
    carried = sum("carried" in ways for _, ways in confirmed)
    return _result(
        summary=(
            f"멈춰 있는 액션아이템: 기한 지남 {overdue}건, "
            f"회의 {service.STALE_AFTER}번 이상 이월 {carried}건, "
            f"{window}일 넘게 확인 필요 {len(waiting)}건."
        ),
        items=items,
        # Only what a person has confirmed is quoted, so only that is sourced.
        evidence=[u for i, _ in confirmed[:MAX_ITEMS] for u in i.source_utterance_ids],
    )


@dataclass(eq=False)
class _Load:
    """One person's confirmed work, counted -- or, with ``user_id`` ``None``, the
    open items nobody on the team holds."""

    user_id: str | None
    name: str
    open: int = 0
    overdue: int = 0
    done: int = 0

    @property
    def weight(self) -> int:
        """Open work, with a late item counted twice: late work is what makes a
        person the one to take something from."""
        return self.open + self.overdue


OVERLOADED_MIN_OPEN = 3
"""A person is "몰림" with at least this many open items *and* at least twice the
team's mean open count, or with two or more overdue. Routing hints for the
Workload subagent (#261 section 3.1), not a measured threshold: the subagent
reads the counts beside them and the manager approves any move."""


def workload_by_owner(session: Session, team_id: str, *, days: int = 30) -> dict[str, Any]:
    """Use this when deciding whether work should move between people -- who holds
    too much open work and who has finished theirs or holds none. Do not use it
    for which items are late or due soon -- that is ``open_action_items``.

    Returns one row per team member: how many confirmed items from the team's
    meetings in the last ``days`` days they hold open, how many of those are
    overdue, and how many they finished. The most loaded come first, then open
    items nobody on the team holds (one "담당 없음" row), then members with
    nothing open. Counts of work only, never speech (#261 section 3.1): nothing
    here says who spoke, how much, or what anyone said. Unconfirmed items are
    not counted -- nobody has agreed yet that they are anyone's work. ``days``
    is a whole number from 1 to 365.
    """
    span = _whole_days(days, low=1)
    if span is None:
        return _not_a_day_count("days")
    today = _today()
    cutoff = datetime.now(UTC) - timedelta(days=span)
    meeting_ids = set(
        session.scalars(
            select(Meeting.id).where(
                Meeting.team_id == team_id,
                service.within_retention(),
                # An upload with no start time cannot be placed in the window;
                # leaving it out would hide its items from everyone.
                or_(Meeting.started_at.is_(None), Meeting.started_at >= cutoff),
            )
        )
    )
    members: dict[str, _Load] = {
        user_id: _Load(user_id=user_id, name=name)
        for user_id, name in session.execute(
            select(User.id, User.display_name)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team_id)
        )
    }
    unowned = _Load(user_id=None, name="담당 없음")
    for status in (*_OPEN, ActionStatus.DONE):
        for i in service.list_action_items(session, status=status):
            if i.meeting_id not in meeting_ids:
                continue
            # A non-member's id is cleared at read time (``needs_reassignment``),
            # so ``None`` here is exactly "nobody on the team holds it".
            load = members.get(i.assignee_id) if i.assignee_id else None
            if status == ActionStatus.DONE:
                # Closed without being finished is nobody's finished work.
                if load is not None and not i.closed_unfinished:
                    load.done += 1
                continue
            load = load or unowned
            load.open += 1
            load.overdue += _overdue(i, today)

    people = list(members.values())
    mean_open = sum(p.open for p in people) / len(people) if people else 0.0
    overloaded = sorted(
        (
            p
            for p in people
            if (p.open >= OVERLOADED_MIN_OPEN and p.open >= 2 * mean_open) or p.overdue >= 2
        ),
        key=lambda p: p.weight,
        reverse=True,
    )
    free = sorted((p for p in people if p.open == 0), key=lambda p: p.done, reverse=True)
    rest = sorted(
        (p for p in people if p not in overloaded and p not in free),
        key=lambda p: p.weight,
        reverse=True,
    )
    # Both ends inside the five, so a team with many loaded people still shows
    # someone who could take work.
    head = overloaded[:3] + ([unowned] if unowned.open else []) + free[:2]
    ordered = head + [p for p in (*overloaded[3:], *free[2:], *rest) if p not in head]
    return _result(
        summary=(
            f"최근 {days}일 회의의 확정 액션아이템 기준, 팀원 {len(people)}명 중 "
            f"몰림 {len(overloaded)}명, 진행 중 0건 {len(free)}명, "
            f"담당 없는 진행 중 항목 {unowned.open}건."
        ),
        items=[_load_finding(p, overloaded, free) for p in ordered],
        # Per-person counts cite no utterance: evidence is for what was said.
        evidence=[],
    )


def _load_finding(
    load: _Load, overloaded: Sequence[_Load], free: Sequence[_Load]
) -> dict[str, Any]:
    tag = " · 몰림" if load in overloaded else " · 여유" if load in free else ""
    body = f"진행 중 {load.open} · 기한 지남 {load.overdue}"
    if load.user_id is not None:
        body += f" · 완료 {load.done}"
    return {
        "title": load.name,
        "body": body + tag,
        "score": float(load.weight),
        "id": load.user_id or "unowned",
        # The same counts as fields, so a subagent reads numbers, not Korean.
        "open": load.open,
        "overdue": load.overdue,
        "done": load.done,
        "state": "loaded" if load in overloaded else "free" if load in free else "",
    }


def open_item_owners(
    session: Session,
    team_id: str,
    *,
    project_id: str | None = None,
    meeting_id: str | None = None,
) -> dict[str, Any]:
    """Use this when deciding whom a piece of work concerns -- for one of the
    team's projects (``project_id``) or for what one earlier meeting left open
    (``meeting_id``), who holds its open action items. Give exactly one of the
    two ids. Do not use it for how much each person holds across the team --
    that is ``workload_by_owner`` -- or for the items themselves -- that is
    ``open_action_items``.

    Returns one row per person who is the assignee of a confirmed, open item
    of that work: how many they hold, how many are overdue, and the nearest
    due date. Most items first; then one "담당 없음" row for open items nobody
    on the team holds. ``summary`` gives the number of people.

    **Work on the board and nothing else** (#756): a person is here because an
    open item is theirs, a fact the team already sees on the board. Nothing
    says who spoke, how much, or who was at a meeting, and nobody is inferred
    for an item with no assignee -- the row says there are such items, not
    whose they might be. Unconfirmed items are not counted: nobody has agreed
    yet that they are anyone's work.
    """
    if (project_id is None) == (meeting_id is None):
        return _result(
            ok=False,
            reason="give exactly one of project_id and meeting_id",
            summary="프로젝트와 회의 가운데 하나만 지정해 주세요.",
            items=[],
            evidence=[],
            confidence=0.0,
        )
    if meeting_id is not None and _team_of(session, meeting_id) != team_id:
        return _not_found("meeting", meeting_id)
    if project_id is not None and (
        session.scalar(
            select(ExtProject.id).where(ExtProject.id == project_id, ExtProject.team_id == team_id)
        )
        is None
    ):
        return _not_found("project", project_id)

    today = _today()
    live = set(
        session.scalars(
            select(Meeting.id).where(Meeting.team_id == team_id, service.within_retention())
        )
    )
    members = {
        user_id: name
        for user_id, name in session.execute(
            select(User.id, User.display_name)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team_id)
        )
    }
    held: dict[str | None, list[ActionItemRead]] = {}
    for status in _OPEN:
        for i in service.list_action_items(session, status=status, meeting_id=meeting_id):
            if i.meeting_id not in live:
                continue
            if project_id is not None and i.project_id != project_id:
                continue
            # A non-member's id is cleared at read time, so ``None`` is exactly
            # "nobody on the team holds it".
            held.setdefault(i.assignee_id if i.assignee_id in members else None, []).append(i)

    people = sorted(
        (user_id for user_id in held if user_id is not None),
        key=lambda user_id: (-len(held[user_id]), members[user_id], user_id),
    )
    rows = [_owner_finding(user_id, members[user_id], held[user_id], today) for user_id in people]
    if None in held:
        rows.append(_owner_finding(None, "담당 없음", held[None], today))
    return _result(
        summary=(
            f"진행 중인 확정 액션아이템의 담당자 {len(people)}명, "
            f"담당 없는 항목 {len(held.get(None, []))}건."
        ),
        items=rows,
        # Who holds what cites no utterance: evidence is for what was said.
        evidence=[],
    )


def _owner_finding(
    user_id: str | None, name: str, items: Sequence[ActionItemRead], today: date
) -> dict[str, Any]:
    overdue = sum(_overdue(i, today) for i in items)
    nearest = min((i.due_date for i in items if i.due_date is not None), default=None)
    body = f"진행 중 {len(items)} · 기한 지남 {overdue}"
    if nearest is not None:
        body += f" · 가장 가까운 기한 {nearest.isoformat()}"
    return {
        "title": name,
        "body": body,
        "score": float(len(items)),
        "id": user_id or "unowned",
        # The same as fields, so a subagent reads numbers, not Korean.
        "open": len(items),
        "overdue": overdue,
        "nearest_due": nearest.isoformat() if nearest is not None else None,
    }


def unresolved_questions(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this after a meeting to list what was asked or objected to and may
    need a follow-up -- "what should someone check or look into". Do not use it
    for commitments; those are ``meeting_action_items``.

    Returns open questions and concerns in spoken order (at most five) with the
    masked utterance text. **These are unreviewed model labels: use them inside
    the agent (L0) -- to draft a briefing a person reads first -- and never post
    them to a channel or tracker on your own** (#261 rule 3). Whether a question
    was answered later in the meeting is not known; say "raised", not "open".
    """
    if service.live_meeting(session, meeting_id) is None:
        return _missing(meeting_id)
    wanted = {UtteranceKind.OPEN_QUESTION, UtteranceKind.CONCERN}
    rows = [c for c in service.classifications_for_meeting(session, meeting_id) if c.kind in wanted]
    texts: dict[str, str] = {
        uid: text
        for uid, text in session.execute(
            select(Utterance.id, Utterance.text).where(
                Utterance.id.in_([c.utterance_id for c in rows[:MAX_ITEMS]])
            )
        )
    }
    questions = sum(c.kind == UtteranceKind.OPEN_QUESTION for c in rows)
    items = [
        {
            "title": "질문" if c.kind == UtteranceKind.OPEN_QUESTION else "우려",
            "body": texts.get(c.utterance_id, ""),
            "score": c.confidence,
            "id": c.utterance_id,
        }
        for c in rows
    ]
    return _result(
        summary=f"회의에서 나온 질문 {questions}건, 우려 {len(rows) - questions}건 (검토 전).",
        items=items,
        evidence=[c.utterance_id for c in rows[:MAX_ITEMS]],
        confidence=0.5,
    )


def review_state(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this before reporting a meeting's outcome anywhere, or when asked what
    still needs a person. Anything counted here must not be presented as settled.

    Returns counts of decisions awaiting a verdict, action items awaiting
    confirmation and weak agreements whose speaker has not answered, and up to
    five of them by id and kind -- never their text.
    """
    if service.live_meeting(session, meeting_id) is None:
        return _missing(meeting_id)
    review = service.review_for_meeting(session, meeting_id)
    pending_decisions = [d for d in review.decisions if d.status == "pending"]
    waiting_items = [
        i for i in review.action_items if i.status == ActionStatus.NEEDS_CONFIRMATION.value
    ]
    unanswered = [a for a in review.ambiguous_agreements if a.outcome in ("not_asked", "pending")]
    items: list[dict[str, Any]] = [
        {"title": "결정 확인 필요", "body": "", "score": 1.0, "id": d.id} for d in pending_decisions
    ]
    items += [
        {"title": "액션아이템 확인 필요", "body": "", "score": 0.8, "id": i.id}
        for i in waiting_items
    ]
    items += [
        {"title": "약한 동의, 답 없음", "body": "", "score": 0.5, "id": a.utterance_id}
        for a in unanswered
    ]
    return _result(
        summary=(
            f"결정 확인 필요 {len(pending_decisions)}건, 액션아이템 확인 필요 "
            f"{len(waiting_items)}건, 답 없는 약한 동의 {len(unanswered)}건."
        ),
        items=items,
        evidence=[u for d in pending_decisions[:MAX_ITEMS] for u in d.source_utterance_ids],
    )


def meeting_decisions(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this when asked what a meeting decided, or before writing a meeting's
    outcome anywhere. Do not use it for how a decision changed across meetings --
    that is D's decision thread -- or for who should do what -- that is
    ``meeting_action_items``.

    Returns the decisions a person confirmed, in their wording (at most five),
    and in ``summary`` how many still wait for a verdict. A decision nobody
    confirmed, or one held back because its text carries personal data, is
    counted and never quoted (#261 rule 3).
    """
    if service.live_meeting(session, meeting_id) is None:
        return _missing(meeting_id)
    review = service.review_for_meeting(session, meeting_id)
    outbound = service.outbound_for_meeting(session, meeting_id)
    sources = {d.id: d.source_utterance_ids for d in review.decisions}
    pending = sum(d.status == "pending" for d in review.decisions)
    held = sum(b.kind == "decision" for b in outbound.blocked)
    summary = f"확정된 결정 {len(outbound.decisions)}건, 확인 필요 {pending}건."
    if held:
        summary += f" 개인정보가 남아 보낼 수 없는 결정 {held}건."
    return _result(
        summary=summary,
        items=[
            {"title": d.statement, "body": "확정", "score": 1.0, "id": d.id}
            for d in outbound.decisions
        ],
        evidence=[u for d in outbound.decisions[:MAX_ITEMS] for u in sources.get(d.id, [])],
    )


def person_action_items(session: Session, team_id: str, user_id: str) -> dict[str, Any]:
    """Use this when asked what one person has on their plate -- "what is left for
    박지영", "what is overdue for me". Do not use it to compare people -- that is
    ``workload_by_owner`` -- or for a whole team's deadlines -- that is
    ``open_action_items``.

    Returns the person's open confirmed items from the team's meetings, most
    urgent first (at most five), with open and overdue counts in ``summary``.
    Work state only, the same a task board shows; nothing about speech.
    """
    member = session.scalar(
        select(User.display_name)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id, User.id == user_id)
    )
    if member is None:
        return _result(
            ok=False,
            reason=f"{user_id} is not on team {team_id}",
            summary="이 팀의 팀원이 아닙니다.",
            items=[],
            evidence=[],
            confidence=0.0,
        )
    today = _today()
    meeting_ids = set(
        session.scalars(
            select(Meeting.id).where(Meeting.team_id == team_id, service.within_retention())
        )
    )
    mine = [
        i
        for status in _OPEN
        for i in service.list_action_items(session, assignee_id=user_id, status=status)
        if i.meeting_id in meeting_ids
    ]
    ranked = sorted(mine, key=lambda i: _urgency(i, today), reverse=True)
    overdue = sum(_overdue(i, today) for i in mine)
    return _result(
        summary=f"{member}님의 진행 중 액션아이템 {len(mine)}건, 기한 지남 {overdue}건.",
        items=[_item_finding(i, today) for i in ranked],
        evidence=[u for i in ranked[:MAX_ITEMS] for u in i.source_utterance_ids],
    )


def action_item_status(session: Session, team_id: str, action_item_id: str) -> dict[str, Any]:
    """Use this when asked about one action item by id -- who holds it, when it
    is due, whether it reached Notion. Do not use it to find items; the list
    tools return ids.

    Returns the item as one finding. An item still waiting for confirmation is
    reported as waiting, without its text (#261 rule 3). A status of ``closed``
    is an item closed without being finished -- not done.
    """
    row = session.get(ExtActionItem, action_item_id)
    if row is None or _team_of(session, row.meeting_id) != team_id:
        return _not_found("action item", action_item_id)
    if row.status == ActionStatus.NEEDS_CONFIRMATION.value:
        return _result(
            summary="확인이 필요한 액션아이템입니다. 확정 전이라 내용은 보여주지 않습니다.",
            items=[{"title": "확인 필요", "body": "", "score": 0.8, "id": row.id}],
            evidence=[],
        )
    (read,) = [
        i
        for i in service.list_action_items(session, meeting_id=row.meeting_id)
        if i.id == action_item_id
    ]
    finding = _item_finding(read, _today())
    synced = [r.system for r in read.sync_refs if r.url]
    finding["body"] += f" · {'·'.join(synced)} 연동됨" if synced else " · 외부 연동 없음"
    return _result(summary="액션아이템 1건.", items=[finding], evidence=read.source_utterance_ids)


FOLLOWUP_DESCRIPTION = "후속 회의 잡기"
"""What ``add_followup_item`` writes. Fixed, so a Follow-up proposal carries ids
only and plan mode can queue it (#556, #561); a person may reword it later, which
is why the open-item read keys on ``origin``, never on this text."""

_STILL_OPEN = (ActionStatus.NEEDS_CONFIRMATION.value, *(s.value for s in _OPEN))


def _open_followup(session: Session, team_id: str) -> ExtActionItem | None:
    return session.scalars(
        select(ExtActionItem)
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .where(
            Meeting.team_id == team_id,
            service.within_retention(),
            ExtActionItem.origin == "followup",
            ExtActionItem.status.in_(_STILL_OPEN),
        )
        .order_by(ExtActionItem.id)
        .limit(1)
    ).first()


def _lock_followups(session: Session, team_id: str) -> None:
    """Hold the team's Follow-up lock for the rest of the transaction.

    Two approvals landing together would each find no open item -- neither sees
    the other's uncommitted insert -- and each add one. A transaction-scoped
    advisory lock keyed by team makes the second wait, then see the first's
    item and refuse. Namespaced like ``notion_setup.lock_setup``; PostgreSQL
    only, as SQLite (unit tests) has one writer anyway.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"extraction.followup:{team_id}"},
    )


def open_followup_item(session: Session, team_id: str) -> dict[str, Any]:
    """Use this before proposing a follow-up meeting: whether the team still has
    a Follow-up item open -- waiting for confirmation, to do or in progress.
    Propose nothing while one is open (#561).

    One answer per team, not per meeting or topic. Reports the item by id and
    status only, never its text (#261 rule 3; the id is all a caller needs).
    """
    row = _open_followup(session, team_id)
    if row is None:
        return _result(summary="열린 후속 회의 항목이 없습니다.", items=[], evidence=[])
    return _result(
        summary="열린 후속 회의 항목이 있습니다.",
        items=[{"title": "후속 회의 항목", "body": row.status, "score": 1.0, "id": row.id}],
        evidence=[],
    )


MAX_HOLIDAY_RANGE_DAYS = 366
"""The longest range ``public_holidays`` answers, from its first day to its
last. A year, leap day included: nothing here plans further ahead, and a wider
one is a wrong argument rather than a question."""


def _not_a_range(reason: str, summary: str) -> dict[str, Any]:
    """The refusal for a range ``public_holidays`` cannot answer. It names the
    argument and never repeats the value, as ``_not_a_day_count`` does."""
    return _result(ok=False, reason=reason, summary=summary, items=[], evidence=[], confidence=0.0)


def public_holidays(session: Session, start: str, end: str) -> dict[str, Any]:
    """Use this when a day has to be chosen and must not fall on a public holiday
    -- a follow-up meeting's suggested date (#964, #985). Do not use it to learn
    whether a person is away or a team is off: nothing here is about a person
    or a team, and it takes neither.

    ``start`` and ``end`` are ``YYYY-MM-DD``; both days are in the range, which
    may be at most ``MAX_HOLIDAY_RANGE_DAYS`` days long.

    Returns one row, ``공휴일``, whose ``days`` lists Korea's public holidays in
    the range as ISO dates, earliest first -- substitute holidays and election
    days among them, and a holiday that falls on a weekend too. One row holding
    them all, because a row a day would be cut at five. The row is there with
    an empty list when the range has none: that is an answer, and not the same
    thing as a refusal.

    They are the days B holds its own digests back on
    (``days_off.is_public_holiday``): Google's public calendar of Korea's
    holidays as it was last read and stored, or the table in code while there
    is no read from the last two weeks. Dates of public record -- no call
    leaves when this is asked, and nothing is read or said about anybody.

    ``ok: false`` for a date that is not one, an ``end`` before ``start``, or a
    longer range.
    """
    try:
        first = date.fromisoformat(start)
    except (TypeError, ValueError):
        return _not_a_range("start is not a date", "시작일이 날짜 형식이 아닙니다 (YYYY-MM-DD).")
    try:
        last = date.fromisoformat(end)
    except (TypeError, ValueError):
        return _not_a_range("end is not a date", "종료일이 날짜 형식이 아닙니다 (YYYY-MM-DD).")
    if last < first:
        return _not_a_range("end is before start", "종료일이 시작일보다 앞입니다.")
    if (last - first).days >= MAX_HOLIDAY_RANGE_DAYS:
        return _not_a_range(
            f"the range is longer than {MAX_HOLIDAY_RANGE_DAYS} days",
            f"기간은 {MAX_HOLIDAY_RANGE_DAYS}일 이내여야 합니다.",
        )
    days = days_off.public_holidays_between(session, first, last, now=datetime.now(UTC))
    return _result(
        summary=f"{first.isoformat()} ~ {last.isoformat()} 공휴일 {len(days)}일.",
        items=[{"title": "공휴일", "days": [day.isoformat() for day in days]}],
        # Public dates cite no utterance: evidence is for what was said.
        evidence=[],
    )


def _team_of(session: Session, meeting_id: str) -> str | None:
    """The meeting's team -- ``None`` for a meeting that is not there or is past
    its retention window (``service.within_retention``, #656), so every tool
    that asks treats the two alike."""
    return session.scalar(
        select(Meeting.team_id).where(Meeting.id == meeting_id, service.within_retention())
    )


def _not_found(kind: str, ident: str) -> dict[str, Any]:
    # The same answer for an unknown id and another team's: an agent run is
    # scoped to one team, and a difference would say the id exists (#189).
    return _result(
        ok=False,
        reason=f"no {kind} {ident} in this team",
        summary="이 팀에서 찾을 수 없습니다.",
        items=[],
        evidence=[],
        confidence=0.0,
    )


TOOLS = [
    meeting_action_items,
    meeting_due_dates,
    open_action_items,
    open_item_owners,
    stalled_action_items,
    workload_by_owner,
    unresolved_questions,
    review_state,
    meeting_decisions,
    person_action_items,
    action_item_status,
    open_followup_item,
    public_holidays,
]
"""Collected by the agent layer by iterating modules (invariant 6), never registered by hand."""

RUN_SCOPE = ("team_id",)
"""Arguments the run fills from its authenticated scope, never the model.

Every tool and action here that takes ``team_id`` trusts it to be the team the
run is for; a model that could choose it could read or change another team's
work. The main agent binds these the way LangChain's ``ToolRuntime`` context or
an injected argument does -- hidden from the model's schema -- and routes only
the rest to it (review of #449)."""


# --- actions: B's writes, for the main agent to run after approval ---------------
#
# agent-layer.md section 8, rule 2: the agent never edits an item itself; it asks
# the module that owns it, and B's own service applies the change with the same
# checks and the same after-commit sync the board uses. Every action here but
# ``add_action_item`` is L2: the main agent proposes it in plan mode (or behind
# LangChain's HumanInTheLoopMiddleware, ``interrupt_on``), and runs it only when
# a person approves. ``add_action_item`` is L1 (``L1_ACTIONS``): it only drafts.
# None is in ``TOOLS``, so a model never calls one directly, and none deletes
# anything (L3 is forbidden). Each owns its transaction, like a board
# request: it commits, then syncs, and a sync failure never undoes the change.


def _acted(summary: str, ident: str) -> dict[str, Any]:
    return _result(
        summary=summary,
        items=[{"title": summary, "body": "", "score": 1.0, "id": ident}],
        evidence=[],
    )


def _refused(reason: str, summary: str) -> dict[str, Any]:
    return _result(ok=False, reason=reason, summary=summary, items=[], evidence=[], confidence=0.0)


def _as_date(value: date | str | None) -> date | None:
    """A model hands a date over as text; ``2026-10-02`` and nothing looser."""
    return date.fromisoformat(value) if isinstance(value, str) else value


def _on_team(session: Session, team_id: str, user_id: str) -> bool:
    return (
        session.scalar(
            select(TeamMember.id).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
        is not None
    )


def _change_item(
    team_id: str,
    action_item_id: str,
    payload: ActionItemUpdate,
    *,
    meeting_id: str | None = None,
) -> dict[str, Any] | str:
    """Apply ``payload`` to one of the team's items; the new status, or a refusal.

    ``meeting_id``, when the caller names one, is the item's meeting: an item
    of another meeting reads as missing, the same as one of another team.
    """
    with session_scope() as session:
        row = session.get(ExtActionItem, action_item_id)
        if row is None or _team_of(session, row.meeting_id) != team_id:
            return _not_found("action item", action_item_id)
        if meeting_id is not None and row.meeting_id != meeting_id:
            return _not_found("action item", action_item_id)
        assignee = payload.assignee_id
        if assignee is not None and not _on_team(session, team_id, assignee):
            # The same rule ``service.require_assignable`` keeps for every
            # write; asked here first so the agent gets a refusal it can say
            # to a person rather than a validation error.
            return _refused(
                f"{assignee} is not on team {team_id}", "그 사람은 이 팀의 팀원이 아닙니다."
            )
        service.update_action_item(session, row, payload)
        status = row.status
    if status != ActionStatus.NEEDS_CONFIRMATION.value:
        tasks.sync_after_confirmation(action_item_id)
    return status


def confirm_action_item(team_id: str, action_item_id: str) -> dict[str, Any]:
    """Confirm an item waiting for confirmation, making it a to-do. It then goes to
    Notion and its assignee's calendar, as when a person confirms it on the board.

    L2 -- runs only after a person approves. Use when a person asks to accept a
    proposed item. Refused for an item that is already confirmed.
    """
    with session_scope() as session:
        row = session.get(ExtActionItem, action_item_id)
        if row is None or _team_of(session, row.meeting_id) != team_id:
            return _not_found("action item", action_item_id)
        if row.status != ActionStatus.NEEDS_CONFIRMATION.value:
            return _refused("already confirmed", "이미 확정된 액션아이템입니다.")
    result = _change_item(team_id, action_item_id, ActionItemUpdate(status=ActionStatus.TODO))
    return (
        result if isinstance(result, dict) else _acted("액션아이템을 확정했습니다.", action_item_id)
    )


def reassign_action_item(
    team_id: str, action_item_id: str, assignee_id: str, meeting_id: str | None = None
) -> dict[str, Any]:
    """Give an item to another member of the team -- what the Workload subagent
    proposes when one person holds too much.

    L2 -- runs only after a person (the manager, for Workload) approves. The new
    assignee must be on the team. ``meeting_id`` is optional: the item's own
    meeting, named so that the proposal is filed under that meeting and not
    the one whose processing woke the run (#959). An item that is not that
    meeting's is refused.
    """
    result = _change_item(
        team_id,
        action_item_id,
        ActionItemUpdate(assignee_id=assignee_id),
        meeting_id=meeting_id,
    )
    return result if isinstance(result, dict) else _acted("담당자를 바꿨습니다.", action_item_id)


def set_action_item_due_date(
    team_id: str,
    action_item_id: str,
    due_date: date | str | None,
    meeting_id: str | None = None,
) -> dict[str, Any]:
    """Move an item's due date, or clear it with ``None``. The assignee's calendar
    event and the Notion page follow.

    L2 -- runs only after a person approves. ``due_date`` is ``YYYY-MM-DD``.
    ``meeting_id`` is optional: the item's own meeting, named so that the
    proposal is filed under that meeting and not the one whose processing woke
    the run (#959). An item that is not that meeting's is refused.
    """
    try:
        due = _as_date(due_date)
    except ValueError:
        return _refused(f"not a date: {due_date!r}", "날짜 형식이 아닙니다 (YYYY-MM-DD).")
    result = _change_item(
        team_id, action_item_id, ActionItemUpdate(due_date=due), meeting_id=meeting_id
    )
    return result if isinstance(result, dict) else _acted("기한을 바꿨습니다.", action_item_id)


def set_action_item_status(team_id: str, action_item_id: str, status: str) -> dict[str, Any]:
    """Move a confirmed item between to-do, in progress and done.

    L2 -- runs only after a person approves. ``status`` is ``todo``,
    ``in_progress`` or ``done``; confirming is ``confirm_action_item``.
    """
    moves = {ActionStatus.TODO, ActionStatus.IN_PROGRESS, ActionStatus.DONE}
    if status not in {s.value for s in moves}:
        return _refused(f"not a status: {status!r}", "todo, in_progress, done 중 하나여야 합니다.")
    result = _change_item(team_id, action_item_id, ActionItemUpdate(status=ActionStatus(status)))
    return result if isinstance(result, dict) else _acted("상태를 바꿨습니다.", action_item_id)


def close_action_item(team_id: str, action_item_id: str) -> dict[str, Any]:
    """Close a confirmed item that will not be finished -- dropped, overtaken, or
    no longer needed. It leaves the open work like a finished one, and is kept
    apart from finished work: its holder's morning DM and work report say it
    was closed, not that they finished it, and the board marks it 닫힘.

    L2 -- runs only after a person approves. For work that was done use
    ``set_action_item_status`` with ``done``. Refused for an item still
    waiting for confirmation, for one already done and for one already closed.
    """
    with session_scope() as session:
        # Held to the commit: a second close, or an edit of the status, waits
        # and then reads what this one left (review of #979).
        row = session.get(ExtActionItem, action_item_id, with_for_update=True)
        if row is None or _team_of(session, row.meeting_id) != team_id:
            return _not_found("action item", action_item_id)
        if row.status == ActionStatus.NEEDS_CONFIRMATION.value:
            return _refused("not confirmed", "확정되지 않은 액션아이템은 닫을 수 없습니다.")
        if row.status == ActionStatus.DONE.value and service.closed_unfinished(session, [row.id]):
            return _refused("already closed", "이미 닫힌 액션아이템입니다.")
        if not service.close_without_finishing(session, row):
            return _refused("already done", "이미 완료된 액션아이템입니다.")
    # Its copies outside follow as they follow any change of status.
    tasks.sync_after_confirmation(action_item_id)
    return _acted("액션아이템을 끝내지 않고 닫았습니다.", action_item_id)


def add_action_item(
    team_id: str,
    meeting_id: str,
    utterance_id: str,
    assignee_id: str | None = None,
    due_date: date | str | None = None,
) -> dict[str, Any]:
    """Draft an item from one thing said in a meeting -- "make that line an
    action item". The summary is written from the lines around it the way the
    pipeline writes one, and the item waits for confirmation with the original
    utterances under it; once confirmed, only the summary is shown and only the
    summary ever leaves (``service.originals_hidden``).

    L1 -- runs without waiting for approval: it makes a draft that reaches
    nobody until a person confirms it on the board (or approves
    ``confirm_action_item``), and deleting a draft undoes it. Ids only: nothing
    a model writes becomes the item's text. Assignee defaults to the speaker,
    ``due_date`` (``YYYY-MM-DD``) to the first date phrase in the line. Refused
    for an utterance whose speaker did not consent to analysis.
    """
    try:
        due = _as_date(due_date)
    except ValueError:
        return _refused(f"not a date: {due_date!r}", "날짜 형식이 아닙니다 (YYYY-MM-DD).")
    with session_scope() as session:
        if _team_of(session, meeting_id) != team_id:
            return _not_found("meeting", meeting_id)
        if assignee_id is not None and not _on_team(session, team_id, assignee_id):
            return _refused(
                f"{assignee_id} is not on team {team_id}", "그 사람은 이 팀의 팀원이 아닙니다."
            )
        window = service.chat_draft_window(session, meeting_id, utterance_id)
        roster = service.team_roster(session, meeting_id)
    if window is None:
        return _not_found("consenting utterance", utterance_id)
    # Model inference outside any session, as ``tasks._extract`` runs it; the
    # resolver replaces the team's names before anything leaves (#411).
    resolver = get_resolver()
    give_roster(resolver, roster)
    resolution = service.resolve_commitment_summaries(resolver, window)[utterance_id]
    with session_scope() as session:
        row = service.create_chat_item(
            session,
            meeting_id=meeting_id,
            utterance_id=utterance_id,
            resolution=resolution,
            assignee_id=assignee_id,
            due_date=due,
        )
        new_id = row.id
    return _acted("액션아이템 초안을 만들었습니다 (확인 필요).", new_id)


def add_followup_item(
    team_id: str,
    meeting_id: str,
    due_date: str | None = None,
    basis: str | None = None,
) -> dict[str, Any]:
    """Add "후속 회의 잡기" to a meeting -- what the Follow-up subagent proposes
    after a meeting that left topics open (#561). It starts waiting for
    confirmation, so it reaches nobody until someone confirms it.

    ``due_date`` (``YYYY-MM-DD``, optional) is the day Follow-up recommends
    for that meeting, which the team lead saw on the card they approved
    (#853). It becomes the item's due date and nothing else: no assignee is
    set, so nothing goes to anybody's calendar and nobody is reminded until a
    person confirms the item and gives it to someone -- from there it is an
    ordinary item with a date. Text that is not a date is refused. A date
    that has already passed, in Korea, is left off and the item is made
    without one: the approval was for the item, a recommendation that is no
    longer one should not fail it, and an item born overdue would be the
    first thing its assignee is reminded about.

    ``basis`` (optional) is what Follow-up took its date from -- ``confirmed``
    or ``draft`` due dates, or the team's meeting ``cadence`` (#963, #966).
    The approval card shows it; B has no use for it. **It is accepted and
    nothing else**: not stored, not put in the item, not sent or logged, and
    not checked -- any value passes, because the proposal was approved with
    it and an argument this did not declare would be refused at the approval
    step before the item is made. The item is the same whatever it says.

    L2 -- runs only after a person (the team lead, for Follow-up) approves. B
    writes the text, so the proposal carries ids only. Recorded as Follow-up's
    (``origin`` ``followup``), not a person's, so edit cost does not count it as
    an item the model missed. Refused while the team already has one open
    (``open_followup_item``), so a second approved proposal makes no second item
    -- even two approved at the same instant: the check and the insert run under
    the team's lock (``_lock_followups``).
    """
    try:
        due = _as_date(due_date)
    except ValueError:
        return _refused(f"not a date: {due_date!r}", "날짜 형식이 아닙니다 (YYYY-MM-DD).")
    passed = due is not None and due < _today()
    if passed:
        due = None
    with session_scope() as session:
        if _team_of(session, meeting_id) != team_id:
            return _not_found("meeting", meeting_id)
        _lock_followups(session, team_id)
        if _open_followup(session, team_id) is not None:
            return _refused(
                f"team {team_id} already has an open follow-up item",
                "이미 열린 후속 회의 항목이 있습니다.",
            )
        row = service.create_action_item(
            session,
            ActionItemCreate(meeting_id=meeting_id, description=FOLLOWUP_DESCRIPTION, due_date=due),
            origin="followup",
        )
        new_id = row.id
    if passed:
        return _acted(
            "후속 회의 항목을 추가했습니다 (확인 필요). "
            "추천 날짜가 이미 지나 기한은 넣지 않았습니다.",
            new_id,
        )
    return _acted("후속 회의 항목을 추가했습니다 (확인 필요).", new_id)


def review_decision(team_id: str, decision_id: str, verdict: str) -> dict[str, Any]:
    """Confirm or reject a decision the model proposed. A confirmed one goes to
    Notion, as when a person confirms it on the review screen; rejecting one
    that was confirmed takes its page out of Notion (#669).

    L2 -- runs only after a person approves. ``verdict`` is ``confirmed`` or
    ``rejected``.
    """
    if verdict not in ("confirmed", "rejected"):
        return _refused(f"not a verdict: {verdict!r}", "confirmed 또는 rejected여야 합니다.")
    with session_scope() as session:
        decision = session.get(ExtDecision, decision_id)
        if decision is None or _team_of(session, decision.meeting_id) != team_id:
            return _not_found("decision", decision_id)
        had_page = service.decision_has_page(session, decision_id)
        service.review_decision(session, decision, DecisionReviewUpdate(status=verdict))  # type: ignore[arg-type]
    if verdict == "confirmed" or had_page:
        tasks.sync_decision_after_confirmation(decision_id)
    return _acted(
        "결정을 확정했습니다." if verdict == "confirmed" else "결정을 기각했습니다.", decision_id
    )


ACTIONS = [
    confirm_action_item,
    reassign_action_item,
    set_action_item_due_date,
    set_action_item_status,
    close_action_item,
    add_action_item,
    add_followup_item,
    review_decision,
]
"""B's writes, L2 but for ``L1_ACTIONS`` (see above). Kept out of ``TOOLS`` on
purpose: the registry offers ``TOOLS`` to models, and the action executor alone
runs these."""

L1_ACTIONS = [add_action_item]
"""The one write that runs without approval: a draft nobody sees outside the
board until a person confirms it (agent-layer.md section 8, L1 "reversible
write"). The agent layer runs an ``ACTIONS`` entry listed here at once and
queues every other one for a person (``collect_actions``)."""
