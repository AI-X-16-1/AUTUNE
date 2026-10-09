"""The follow-up meeting, once Follow-up's proposal is approved.

The Follow-up subagent proposes a follow-up meeting after a meeting that left
topics open, with a day (``agent/docs/specs/2026-09-30-followup-subagent-design.md``).
When the team lead approves, the agent layer runs ``tools.schedule_followup_meeting``
as the approver, and this puts that meeting on the approver's own Google
Calendar and tells the team's Slack channel -- what "다음 회의 잡기" (#824)
does for an event somebody already had, for an event nobody has made yet.

**The approver's own calendar, with their own grant** (#435's rule): approving
the proposal is their act, and the meeting is one they now organise. The
event's guests are the meeting's participants who resolved to a member still
on its team, the approver aside -- Google invites them (``sendUpdates=all``).
Nobody outside the team is ever invited, so the agenda in the description
reaches the team only, the rule ``calendar_writes.outside_team`` keeps for
"다음 회의 잡기".

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
starts, how many were invited, and the gaps on its agenda
(``team_notice.post_followup``). **Each guest is sent a Slack DM** too, the
same news addressed to them (``team_notice.dm_followup``), with the team's
Slack connection and the account they linked themselves; a guest who linked
none gets the calendar invitation alone. A Slack that fails leaves the event
made.

What leaves for Google and Slack is the meeting's title, the gaps' titles and
questions (stored masked) and the guests' addresses, all through
``autune_integrations``' outbound check. Logs hold ids, counts and outcomes.
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
from autune_core import Meeting, Participant, TeamMember, User, get_logger
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
    "failed",
]
"""``already_scheduled``: the meeting has its follow-up event; nothing was
made. ``past_day``: the approved day is before today in Korea."""


@dataclass(frozen=True)
class Scheduled:
    outcome: Outcome
    starts: datetime | None = None
    invited: int = 0
    gaps: int = 0
    slack: team_notice.SlackOutcome = "not_tried"
    dms: int = 0
    """Guests sent a Slack DM."""


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


def guests(session: Session, meeting: Meeting, *, organizer: str) -> dict[str, str]:
    """Who to invite, by user id: the meeting's participants who resolved to a
    member still on its team, the organizer aside, each with their address in
    lower case. A member with no address is left out."""
    rows = session.execute(
        select(User.id, User.email)
        .join(Participant, Participant.user_id == User.id)
        .join(
            TeamMember,
            (TeamMember.user_id == User.id) & (TeamMember.team_id == meeting.team_id),
        )
        .where(Participant.meeting_id == meeting.id, User.id != organizer)
    )
    found = {user_id: email.strip().lower() for user_id, email in rows if email and email.strip()}
    return dict(sorted(found.items(), key=lambda pair: pair[1]))


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
    invited = guests(session, meeting, organizer=approver.id)
    starts, ends = window(meeting, day)
    body = {
        "summary": TITLE.format(title=meeting.title),
        "description": "\n".join(calendar_writes.agenda_line(gap) for gap in gaps),
        "start": {"dateTime": starts.isoformat(), "timeZone": "Asia/Seoul"},
        "end": {"dateTime": ends.isoformat(), "timeZone": "Asia/Seoul"},
        "attendees": [{"email": email} for email in invited.values()],
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
                    params={"sendUpdates": "all", "fields": CREATE_FIELDS},
                    json=body,
                )
                event_id = str(made.get("id") or "")
                if event_id:
                    row.calendar_id, row.event_id = calendar_id, event_id
                    outcome = "scheduled"
    except ReconnectRequiredError:
        outcome = "reconnect_required"
    except (IntegrationError, PrivacyViolationError) as exc:
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
    slack = team_notice.post_followup(
        session, meeting, gaps, approver=approver, starts=starts, invited=len(invited)
    )
    dms = team_notice.dm_followup(
        session, meeting, gaps, approver=approver, starts=starts, guests=list(invited)
    )
    log.info(
        "gap_followup_scheduled",
        meeting_id=meeting.id,
        gaps=len(gaps),
        invited=len(invited),
        slack=slack,
        dms=dms,
    )
    return Scheduled(
        "scheduled",
        starts=starts,
        invited=len(invited),
        gaps=len(gaps),
        slack=slack,
        dms=dms,
    )
