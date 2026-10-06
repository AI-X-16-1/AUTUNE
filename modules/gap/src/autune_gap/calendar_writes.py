"""S20's two Google Calendar writes (#824).

- **"다음 회의 잡기"** (beside the template rail's heading) adds one line per
  open gap of the meeting to the description of the team's next meeting, on
  the calendar of whoever pressed it. The per-gap carry route writes the same
  line for one gap. The next meeting
  is the team's next ``scheduled`` meeting in Autune; its event is the one on
  that person's calendar that starts when the meeting does. Nothing links a
  meeting to an event id, so the start time is the link, and a person whose
  calendar has no such event gets the mark alone.
- **"담당자 지정해 질문"** puts the gap's question on one teammate's calendar as
  an all-day event -- no attendees, so nobody is sent an invitation, the way
  module B puts a due date on its assignee's calendar.

Each write goes through that person's own grant (``user_integrations``, core)
and module B's refresh rules, copied here because invariant 2 forbids calling
B's: a grant issued to another Google client, or one recorded as revoked, is
reported without asking Google.

What leaves for Google is the gap's title and its question, both stored masked
(``graph.build_topics`` refuses a label holding a masked span), and the gap id
so a line can be found again. Every request goes through
``autune_integrations``, whose outbound check runs on the whole body -- for the
agenda that includes the description the event already had.

A calendar failure never undoes the mark. The outcome is returned for the
screen to say, and logged by kind only: an exception message from Google can
quote the event.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_core import Meeting, get_logger, load_user_integration
from autune_core.errors import PrivacyViolationError
from autune_core.settings import get_settings as get_core_settings
from autune_integrations import (
    CalendarClient,
    IntegrationError,
    ReconnectRequiredError,
    refresh_access_token,
)

from .models import GapGap

log = get_logger(__name__)

CALENDAR = "calendar"

AgendaOutcome = Literal[
    "added",
    "removed",
    "no_next_meeting",
    "no_event",
    "not_connected",
    "reconnect_required",
    "failed",
]
"""What happened to the next meeting's event. ``no_next_meeting``: the team has
no scheduled meeting ahead. ``no_event``: it has one, and the presser's
calendar holds no event starting then."""

AskOutcome = Literal["added", "already_asked", "not_connected", "reconnect_required", "failed"]

MATCH_WINDOW = timedelta(minutes=5)
"""How far an event's start may sit from the meeting's and still be it."""

AGENDA_PREFIX = "[Autune 갭]"
QUESTION_PREFIX = "[질문]"
QUESTION_FOOTER = "Autune 회의 분석에서 온 질문입니다."
TAG = ("autune", "1")
GAP_KEY = "autune_gap"


@contextmanager
def calendar_of(session: Session, user_id: str) -> Iterator[tuple[CalendarClient, str] | None]:
    """This person's own calendar client and calendar id, or ``None`` when they
    have not connected one. Raises ``ReconnectRequiredError`` for a grant that
    is known gone or refused. Closes the client on the way out."""
    client_id, client_secret = get_core_settings().google_integration_credentials
    config = load_user_integration(session, user_id, CALENDAR)
    if config is None or not config.secret or not client_id or not client_secret:
        yield None
        return
    issued_to = config.config.get("client_id")
    if issued_to and issued_to != client_id:
        raise ReconnectRequiredError("the calendar grant was issued to another Google client")
    if config.config.get("grant_revoked"):
        raise ReconnectRequiredError("the calendar grant was revoked")
    token = refresh_access_token(
        client_id=client_id, client_secret=client_secret, refresh_token=config.secret
    )
    client = CalendarClient(token)
    try:
        yield client, str(config.config.get("calendar_id") or "primary")
    finally:
        client.close()


def next_meeting(session: Session, team_id: str, *, now: datetime) -> Meeting | None:
    """The team's next scheduled meeting -- ``started_at`` doubles as the
    scheduled start, as context's briefs read it."""
    return session.scalar(
        select(Meeting)
        .where(
            Meeting.team_id == team_id,
            Meeting.status == "scheduled",
            Meeting.started_at.is_not(None),
            Meeting.started_at > now,
        )
        .order_by(Meeting.started_at)
        .limit(1)
    )


def agenda_line(gap: GapGap) -> str:
    """One line per gap. It ends with the gap id, which is how taking the gap
    back finds the line again."""
    question = f" — {gap.suggested_question}" if gap.suggested_question else ""
    return f"{AGENDA_PREFIX} {gap.title}{question} ({gap.id})"


def with_line(description: str, line: str, gap_id: str) -> str | None:
    """The description with the gap's line added, or ``None`` if it is there."""
    if any(_is_line_of(existing, gap_id) for existing in description.splitlines()):
        return None
    return f"{description.rstrip()}\n{line}" if description.strip() else line


def without_line(description: str, gap_id: str) -> str | None:
    """The description with the gap's line taken out, or ``None`` if it has none."""
    lines = description.splitlines()
    kept = [line for line in lines if not _is_line_of(line, gap_id)]
    return None if len(kept) == len(lines) else "\n".join(kept)


def _is_line_of(line: str, gap_id: str) -> bool:
    return line.startswith(AGENDA_PREFIX) and line.rstrip().endswith(f"({gap_id})")


def edited(description: str, gaps: Sequence[GapGap], *, carried: bool) -> str | None:
    """The description with every gap's line added (or taken out), or ``None``
    when nothing changes -- so an event is not written for nothing."""
    text, changed = description, False
    for gap in gaps:
        updated = (
            with_line(text, agenda_line(gap), gap.id) if carried else without_line(text, gap.id)
        )
        if updated is not None:
            text, changed = updated, True
    return text if changed else None


def update_agenda(
    session: Session,
    gaps: Sequence[GapGap],
    *,
    team_id: str,
    user_id: str,
    carried: bool,
    now: datetime | None = None,
) -> AgendaOutcome:
    """Add the gaps' lines to the next meeting's event, or take them out."""
    meeting = next_meeting(session, team_id, now=now or datetime.now(UTC))
    if meeting is None or meeting.started_at is None:
        return "no_next_meeting"
    # SQLite hands back a naive datetime; PostgreSQL's is already UTC-aware.
    starts = (
        meeting.started_at if meeting.started_at.tzinfo else meeting.started_at.replace(tzinfo=UTC)
    )
    try:
        with calendar_of(session, user_id) as calendar:
            if calendar is None:
                return "not_connected"
            client, calendar_id = calendar
            events = client.list_events(
                calendar_id, starts - MATCH_WINDOW, starts + MATCH_WINDOW, limit=10
            )
            event = next(
                (
                    e
                    for e in events
                    if isinstance(e.start, datetime) and abs(e.start - starts) <= MATCH_WINDOW
                ),
                None,
            )
            if event is None:
                return "no_event"
            path = f"/calendars/{calendar_id}/events/{event.id}"
            current = str(client.request("GET", path).get("description") or "")
            updated = edited(current, gaps, carried=carried)
            if updated is not None:
                client.request("PATCH", path, json={"description": updated})
    except ReconnectRequiredError:
        return "reconnect_required"
    except (IntegrationError, PrivacyViolationError) as exc:
        log.warning("gap_agenda_failed", meeting_id=meeting.id, error=type(exc).__name__)
        return "failed"
    log.info("gap_agenda_set", meeting_id=meeting.id, gaps=len(gaps), carried=carried)
    return "added" if carried else "removed"


def ask_on_calendar(
    session: Session, gap: GapGap, *, user_id: str, day: date
) -> tuple[AskOutcome, str | None, str | None]:
    """Put the gap's question on this person's calendar on ``day``. Returns the
    outcome and, when added, the calendar id and event id."""
    summary = f"{QUESTION_PREFIX} {gap.title}"
    body = gap.suggested_question or gap.title
    try:
        with calendar_of(session, user_id) as calendar:
            if calendar is None:
                return "not_connected", None, None
            client, calendar_id = calendar
            event_id = client.create_all_day_event(
                calendar_id,
                summary,
                day,
                description=f"{body}\n\n{QUESTION_FOOTER}",
                private={TAG[0]: TAG[1], GAP_KEY: gap.id},
            )
    except ReconnectRequiredError:
        return "reconnect_required", None, None
    except (IntegrationError, PrivacyViolationError) as exc:
        log.warning("gap_question_failed", gap_id=gap.id, error=type(exc).__name__)
        return "failed", None, None
    return "added", calendar_id, event_id


def next_working_day(today: date) -> date:
    """Tomorrow, or Monday when tomorrow is a weekend day."""
    day = today + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day
