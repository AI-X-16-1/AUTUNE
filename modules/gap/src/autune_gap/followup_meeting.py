"""The follow-up meeting, once Follow-up's proposal is approved.

The Follow-up subagent proposes a follow-up meeting after a meeting that left
topics open, with a day (``agent/docs/specs/2026-09-30-followup-subagent-design.md``).
When the team lead approves, the agent layer runs ``tools.schedule_followup_meeting``
as the approver, and this puts that meeting on the approver's own Google
Calendar and tells the team's Slack channel -- what "다음 회의 잡기" (#824)
does for an event somebody already had, for an event nobody has made yet.

**The approver's own calendar, with their own grant** (#435's rule): approving
the proposal is their act, and the meeting is one they now organise.
**Nobody is invited** and nobody else is sent anything: the event has no
guests and Google is told to send no notice (``sendUpdates=none``). Inviting
the meeting's members sends their addresses to Google and mails them from
the approver's account; DMing them is a new kind of message. Both wait for
the team's decision after 10/12 (#756, #1046), and the legal notice comes
first (mkkim68 and kjfcvx12 on #1106). The approver invites people from
their own calendar if they want to.

**The event.** Its title is ``후속 회의 · <meeting title>``. It starts on the
approved day at the clock time the meeting started, in Korea, and lasts as
long as the meeting did, in half hours from 30 minutes to two hours; a
meeting with no start time starts at 10:00 and one with no length takes an
hour. Its description is one line per open gap -- not dismissed and not
``low``, what S20 shows -- in the form "다음 회의 잡기" writes, and each line is
recorded in ``GapAgendaEvent``, so it comes out when the gap is taken back,
the meeting goes or the account goes, as every other line does. Those gaps
are marked sent on (``carried_at``), as "다음 회의 잡기" marks them.

**Once per meeting.** ``GapFollowupEvent`` is written before Google is asked,
under a unique ``meeting_id``, so a second approval makes no second event and
no second notice. A request Google refuses takes the row back, so a later
proposal for the meeting can still make it once the calendar is fixed.

**The team channel is told once** the calendar took the event: when it
starts and the gaps on its agenda (``team_notice.post_followup``). A Slack
that fails leaves the event made.

What leaves for Google and Slack is the meeting's title and the gaps' titles
and questions (stored masked), all through ``autune_integrations``' outbound
check. **A refusal by that check is not a Google failure**: every value is one
Autune stored, so a refusal is a finding about the store. It is logged as an
error with the meeting's id (``gap_followup_refused``), the outcome is
``refused``, and nothing is made -- as ``team_notice._post`` tells a refused
notice apart. Other logs hold ids, counts and outcomes.

**Known gap: the event is made before the row is committed.** The row and the
event's id are written with the caller's session, which the agent layer
commits after this returns. Should that commit fail after Google made the
event, the event stays on the approver's calendar with no row, and a later
approval for the meeting would make a second one. Accepted for now: it needs
the database to fail between two statements, the event is on the approver's
own calendar only, and nobody is invited, so nobody else is mailed twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_contracts.enums import GapSeverity
from autune_core import Meeting, User, get_logger
from autune_core.errors import PrivacyViolationError
from autune_integrations import IntegrationError, ReconnectRequiredError

from . import calendar_writes, team_notice
from .models import GapFollowupEvent, GapGap

log = get_logger(__name__)

KST = ZoneInfo("Asia/Seoul")

DEFAULT_START = time(10, 0)
"""When the follow-up starts on its day if the meeting has no start time."""

DEFAULT_LENGTH = timedelta(hours=1)
SHORTEST = timedelta(minutes=30)
LONGEST = timedelta(hours=2)
STEP = timedelta(minutes=30)

TITLE = "후속 회의 · {title}"

CREATE_FIELDS = "id"
"""All the create reads back: the event's id, to record its lines under."""

Outcome = Literal[
    "scheduled",
    "already_scheduled",
    "past_day",
    "not_connected",
    "reconnect_required",
    "refused",
    "failed",
]
"""``already_scheduled``: the meeting has its follow-up event; nothing was
made. ``past_day``: the approved day is before today in Korea. ``refused``:
the outbound check found personal data in what would have gone to Google."""


@dataclass(frozen=True)
class Scheduled:
    outcome: Outcome
    starts: datetime | None = None
    gaps: int = 0
    slack: team_notice.SlackOutcome = "not_tried"


def window(meeting: Meeting, day: date) -> tuple[datetime, datetime]:
    """When the follow-up starts and ends: ``day`` at the meeting's clock time
    in Korea, as long as the meeting was, in half hours within bounds."""
    started = meeting.started_at
    if started is not None and started.tzinfo is None:
        # SQLite hands back a naive datetime; PostgreSQL's is already UTC-aware.
        started = started.replace(tzinfo=UTC)
    clock = started.astimezone(KST).time().replace(second=0, microsecond=0) if started else None
    starts = datetime.combine(day, clock or DEFAULT_START, tzinfo=KST)
    length = DEFAULT_LENGTH
    if meeting.duration_seconds:
        steps = round(timedelta(seconds=meeting.duration_seconds) / STEP)
        length = min(max(STEP * steps, SHORTEST), LONGEST)
    return starts, starts + length


def open_gaps(session: Session, meeting_id: str) -> list[GapGap]:
    """What S20 shows by default: not dismissed and not ``low``, most risky first."""
    return list(
        session.scalars(
            select(GapGap)
            .where(
                GapGap.meeting_id == meeting_id,
                GapGap.dismissed_at.is_(None),
                GapGap.severity != GapSeverity.LOW.value,
            )
            .order_by(GapGap.risk_score.desc(), GapGap.id)
        )
    )


def _claim(session: Session, meeting_id: str, user_id: str, day: date) -> GapFollowupEvent | None:
    """The meeting's row, written now; ``None`` when it already has one."""
    row = GapFollowupEvent(meeting_id=meeting_id, user_id=user_id, event_day=day)
    try:
        with session.begin_nested():
            session.add(row)
    except IntegrityError:
        return None
    return row


def _release(session: Session, row: GapFollowupEvent) -> None:
    session.execute(delete(GapFollowupEvent).where(GapFollowupEvent.id == row.id))
    session.expunge(row)


def schedule(
    session: Session,
    meeting: Meeting,
    approver: User,
    day: date,
    *,
    today: date | None = None,
) -> Scheduled:
    """Put the follow-up meeting on the approver's calendar and tell the team.

    Writes with the caller's session, for the caller to commit.
    """
    if day < (today or datetime.now(KST).date()):
        return Scheduled("past_day")
    existing = session.scalar(
        select(GapFollowupEvent.id).where(GapFollowupEvent.meeting_id == meeting.id)
    )
    if existing is not None:
        return Scheduled("already_scheduled")
    row = _claim(session, meeting.id, approver.id, day)
    if row is None:
        return Scheduled("already_scheduled")

    gaps = open_gaps(session, meeting.id)
    starts, ends = window(meeting, day)
    body = {
        "summary": TITLE.format(title=meeting.title),
        "description": "\n".join(calendar_writes.agenda_line(gap) for gap in gaps),
        "start": {"dateTime": starts.isoformat(), "timeZone": "Asia/Seoul"},
        "end": {"dateTime": ends.isoformat(), "timeZone": "Asia/Seoul"},
    }
    outcome: Outcome = "failed"
    try:
        with calendar_writes.calendar_of(session, approver.id) as calendar:
            if calendar is None:
                outcome = "not_connected"
            else:
                client, calendar_id = calendar
                made = client.request(
                    "POST",
                    f"/calendars/{calendar_id}/events",
                    params={"sendUpdates": "none", "fields": CREATE_FIELDS},
                    json=body,
                )
                event_id = str(made.get("id") or "")
                if event_id:
                    row.calendar_id, row.event_id = calendar_id, event_id
                    outcome = "scheduled"
    except ReconnectRequiredError:
        outcome = "reconnect_required"
    except PrivacyViolationError:
        log.error("gap_followup_refused", meeting_id=meeting.id)
        outcome = "refused"
    except IntegrationError as exc:
        log.warning("gap_followup_failed", meeting_id=meeting.id, error=type(exc).__name__)
        outcome = "failed"
    if outcome != "scheduled" or row.calendar_id is None or row.event_id is None:
        _release(session, row)
        log.info("gap_followup_not_scheduled", meeting_id=meeting.id, outcome=outcome)
        return Scheduled(outcome)

    calendar_writes.record_lines(
        session,
        gaps,
        user_id=approver.id,
        calendar_id=row.calendar_id,
        event_id=row.event_id,
        event_day=day,
    )
    now = datetime.now(tz=UTC)
    for gap in gaps:
        if gap.carried_at is None:
            gap.carried_at = now
    session.flush()
    slack = team_notice.post_followup(session, meeting, gaps, approver=approver, starts=starts)
    log.info("gap_followup_scheduled", meeting_id=meeting.id, gaps=len(gaps), slack=slack)
    return Scheduled("scheduled", starts=starts, gaps=len(gaps), slack=slack)
