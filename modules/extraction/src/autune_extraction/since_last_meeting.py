"""What moved on a team's board since its last meeting, read when asked.

The user, 2026-10-12: "사용자들이 회의할 때 전체 작업 내용 요약". This is the
smallest cut of that: Autune's own items, sorted into the parts a team goes
through at the start of a meeting -- what was finished, what was closed without
being finished, what is late, what was started, what is new -- and how many
are open in all. Read from the rows at the request and kept nowhere; no model
reads or writes any of it.

**The last meeting** is the team's most recent one that was analysed
(``ANALYSED``), by when it was held -- ``started_at``, or the upload when
nobody recorded a start, as ``service.meetings_since`` reads it. A meeting
being recorded or analysed now is not it: asked during a meeting, "since the
last meeting" means since the one before. A meeting past its retention window
is not it either, and one that was deleted is gone with its rows, so the
meeting before it becomes the last and the answer reaches further back.

**"Since" is an instant, not a day**: an edit is after it when its
``created_at`` is. Both carry an offset, so the server's zone does not come
into it. Only 늦은 일 asks for a date, and takes Korea's
(``reminders.korean_day``), as the board and every tool of B's do.

**What the parts can say.** ``ext_edit_events`` keeps that an item's status
was edited and when -- never what it was before and never by whom
(``work_report`` reads it the same way). So an item is placed by what it is
now:

- *끝난 일* is done now and its status was edited since -- and it was not
  closed without being finished (``service.closed_unfinished``), which is said
  apart as *끝내지 않고 닫힌 일*, so that nobody's item is reported as finished
  work when it was closed;
- *늦은 일* is open and was due before today, whatever else happened to it;
- *시작한 일* is in progress now and its status was edited since;
- *새로 생긴 일* is 진행 전 now and its first status event is since: confirmed
  from the review, or added by hand, after the last meeting.

An item finished and re-opened since is therefore not finished: it is where it
is now -- started if it is in progress, otherwise only counted as open. One
that was already on the board and was moved back to 진행 전 is counted as open
and listed nowhere. Each item is in one part at most; ``open`` counts every
open item of the team, listed or not.

**Items, never people** (ADR 0003; privacy.md section 3). The answer is item
ids in parts and one team total. It names nobody, groups nothing by assignee
and counts nothing per person; the board draws each id as the card it already
shows. Whoever made an edit is not known here and not said. Nothing is logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, Team

from . import reminders, service
from .models import ExtActionItem, ExtEditEvent
from .schemas import SinceLastMeeting

ANALYSED = ("awaiting_confirmation", "complete", "delivered")
"""Meeting statuses that mean the meeting was held and analysed -- the three
the agent layer's subagents read as analysed. ``recording`` and ``analyzing``
are a meeting still under way, ``scheduled`` one that has not begun."""

_OPEN = (ActionStatus.TODO.value, ActionStatus.IN_PROGRESS.value)


@dataclass(frozen=True)
class LastMeeting:
    id: str
    title: str
    held: datetime


def _aware(moment: datetime) -> datetime:
    """SQLite hands back naive datetimes; compare everything in UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def last_meeting(session: Session, team_id: str, *, now: datetime) -> LastMeeting | None:
    """The team's most recent analysed meeting held by ``now``, or ``None``."""
    held = func.coalesce(Meeting.started_at, Meeting.created_at)
    found = session.execute(
        select(Meeting.id, Meeting.title, held)
        .where(
            Meeting.team_id == team_id,
            service.within_retention(now),
            Meeting.status.in_(ANALYSED),
            held <= now,
        )
        .order_by(held.desc(), Meeting.id.desc())
        .limit(1)
    ).first()
    if found is None:
        return None
    meeting_id, title, when = found
    return LastMeeting(id=meeting_id, title=title, held=_aware(when))


def _status_events(session: Session, team_id: str, since: datetime) -> tuple[set[str], set[str]]:
    """Ids of the team's items with a status event after ``since``, and of those
    with one at or before it. A status event is an edit that named the status,
    a close, or a hand-made item's creation -- each a moment the item's place
    on the board was set."""
    after: set[str] = set()
    before: set[str] = set()
    for item_id, kind, fields, at in session.execute(
        select(
            ExtEditEvent.action_item_id,
            ExtEditEvent.kind,
            ExtEditEvent.fields,
            ExtEditEvent.created_at,
        )
        .join(Meeting, Meeting.id == ExtEditEvent.meeting_id)
        .where(
            Meeting.team_id == team_id,
            ExtEditEvent.action_item_id.is_not(None),
            ExtEditEvent.kind.in_(("created", "edited", "closed")),
        )
    ).tuples():
        if item_id is None:
            continue
        if kind == "edited" and "status" not in (fields or "").split(","):
            continue
        (after if _aware(at) > since else before).add(item_id)
    return after, before


def summary(session: Session, team_id: str, *, now: datetime | None = None) -> SinceLastMeeting:
    """One team's board since its last meeting. The caller has checked the reader."""
    moment = now if now is not None else datetime.now(UTC)
    name = session.scalar(select(Team.name).where(Team.id == team_id)) or ""
    last = last_meeting(session, team_id, now=moment)
    if last is None:
        return SinceLastMeeting(team_id=team_id, team_name=name, state="no_meeting")

    items = list(
        session.scalars(
            select(ExtActionItem)
            .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
            .where(
                Meeting.team_id == team_id,
                service.within_retention(moment),
                ExtActionItem.status.in_((*_OPEN, ActionStatus.DONE.value)),
            )
            .order_by(
                ExtActionItem.due_date.is_(None),
                ExtActionItem.due_date,
                ExtActionItem.created_at,
                ExtActionItem.id,
            )
        )
    )
    changed, earlier = _status_events(session, team_id, last.held)
    not_finished = service.closed_unfinished(
        session,
        {
            item.id
            for item in items
            if item.status == ActionStatus.DONE.value and item.id in changed
        },
    )
    today = reminders.korean_day(moment)

    answer = SinceLastMeeting(
        team_id=team_id,
        team_name=name,
        state="ok",
        meeting_id=last.id,
        meeting_title=last.title,
        since=last.held,
    )
    for item in items:
        if item.status == ActionStatus.DONE.value:
            if item.id in not_finished:
                answer.closed.append(item.id)
            elif item.id in changed:
                answer.finished.append(item.id)
            continue
        answer.open += 1
        if item.due_date is not None and item.due_date < today:
            answer.late.append(item.id)
        elif item.id not in changed:
            continue
        elif item.status == ActionStatus.IN_PROGRESS.value:
            answer.started.append(item.id)
        elif item.id not in earlier:
            answer.created.append(item.id)
    return answer
