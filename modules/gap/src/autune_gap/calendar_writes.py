"""S20's Google Calendar write: "다음 회의 잡기" (#824).

The button beside the template rail's heading lists the presser's own upcoming
events, and adds one line per open gap of the meeting to the description of the
one they pick. Without a pick it falls back to the team's next meeting: the
team's next ``scheduled`` meeting in Autune, whose event is the one on that
person's calendar that starts when the meeting does. Nothing links a meeting to
an event id, so the start time is the link, and a person whose calendar has no
such event gets the mark alone. The per-gap carry route writes the same line
for one gap, and taking a gap back takes its line out.

Only the presser's own calendar, with their own grant (``user_integrations``,
core). "담당자 지정해 질문" does not write to anybody's calendar: using one
person's grant for something that is not their own work is outside #435's rule
(mkkim68 on #824, 2026-10-06). Module B's refresh rules are copied here because
invariant 2 forbids calling B's: a grant issued to another Google client, or one
recorded as revoked, is reported without asking Google.

**What is read is narrowed at Google** (mkkim68 on #824). The picker asks for
each event's id, title, start, end and status only; the write asks the picked
event for its description and its attendees' addresses only. Nothing read is
stored, and logs hold counts.

**An event shared outside the team is refused.** Google shows a description to
everyone on the event, so a line written there would hand the gaps to whoever
else is invited. An event with an attendee who is not on the meeting's team is
not written (``external_attendees``); meeting rooms and the presser themselves
do not count. A guest list Google hides from the presser cannot be checked.

**Every line written is recorded so it can be taken out** (``GapAgendaEvent``):
the gap, whose calendar and which event. Taking the gap back removes the line
and the record; a gap whose question named words a person has since deleted
has its lines queued for removal (``queue_gap_lines``, #587). When the meeting
goes -- deleted or expired -- its records are copied to ``GapAgendaCleanup``
and ``drain_agenda_cleanup`` takes the lines out with each owner's own grant;
when an account goes, its lines are taken out at once, while the grant still
exists. Both best effort, as module B's are (privacy.md section 4).

What leaves for Google is the gap's title and its question, both stored masked
(``graph.build_topics`` refuses a label holding a masked span), and the gap id
so a line can be found again. Every request goes through
``autune_integrations``, whose outbound check runs on the whole body -- which
for a description includes what the event already said.

**Adding lines tells the event's attendees.** The write asks Google to send
its change notice (``sendUpdates=all``) to the people already invited, so the
meeting's members see the next meeting's agenda in their own calendars; nobody
is invited. Those people are all on the team, by the refusal above. Taking
lines out sends nothing.

A calendar failure never undoes the mark. The outcome is returned for the
screen to say, and logged by kind only: an exception message from Google can
quote the event.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from autune_core import (
    Meeting,
    TeamMember,
    User,
    get_logger,
    load_user_integration,
    session_scope,
)
from autune_core.deletion import on_meeting_deleted, on_user_deleted
from autune_core.errors import PrivacyViolationError
from autune_core.settings import get_settings as get_core_settings
from autune_integrations import (
    CalendarClient,
    CalendarEvent,
    IntegrationError,
    PermanentIntegrationError,
    ReconnectRequiredError,
    refresh_access_token,
)

from .models import GapAgendaCleanup, GapAgendaEvent, GapGap

log = get_logger(__name__)

CALENDAR = "calendar"

AgendaOutcome = Literal[
    "added",
    "removed",
    "no_next_meeting",
    "no_event",
    "external_attendees",
    "not_connected",
    "reconnect_required",
    "failed",
]
"""What happened to the next meeting's event. ``no_next_meeting``: the team has
no scheduled meeting ahead. ``no_event``: it has one, and the presser's
calendar holds no event starting then. ``external_attendees``: the event is
shared with somebody outside the meeting's team, so nothing was written."""

MATCH_WINDOW = timedelta(minutes=5)
"""How far an event's start may sit from the meeting's and still be it."""

AGENDA_PREFIX = "[Autune 갭]"

LIST_FIELDS = "items(id,summary,start,end,status),nextPageToken"
"""All the picker reads of an event. Description, attendees and place stay at
Google (mkkim68 on #824)."""

WRITE_FIELDS = "description,attendees(email,self,resource)"
"""All the write reads of the picked event: the description it edits, and the
attendees' addresses to refuse an event shared outside the team."""

DESCRIPTION_FIELDS = "description"
"""All the cleanup reads: the description it takes lines out of."""

CLEANUP_BATCH = 100
"""How many queued lines one ``drain_agenda_cleanup`` run takes."""

CLEANUP_MAX_ATTEMPTS = 5
"""Transient failures before a queued line is given up on and logged."""


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


def without_lines(description: str, gap_ids: Sequence[str]) -> str | None:
    """The description with these gaps' lines taken out, or ``None`` if it has
    none of them."""
    lines = description.splitlines()
    kept = [line for line in lines if not any(_is_line_of(line, g) for g in gap_ids)]
    return None if len(kept) == len(lines) else "\n".join(kept)


def _is_line_of(line: str, gap_id: str) -> bool:
    return line.startswith(AGENDA_PREFIX) and line.rstrip().endswith(f"({gap_id})")


def edited(description: str, gaps: Sequence[GapGap], *, carried: bool) -> str | None:
    """The description with every gap's line added (or taken out), or ``None``
    when nothing changes -- so an event is not written for nothing."""
    if not carried:
        return without_lines(description, [gap.id for gap in gaps])
    text, changed = description, False
    for gap in gaps:
        updated = with_line(text, agenda_line(gap), gap.id)
        if updated is not None:
            text, changed = updated, True
    return text if changed else None


def outside_team(attendees: Sequence[dict[str, Any]], team: set[str]) -> bool:
    """Whether anyone on the event is outside the team: an attendee who is not
    a meeting room, not the calendar's owner, and whose address is not a team
    member's. Addresses compare without case, as Google treats them."""
    for attendee in attendees:
        if attendee.get("resource") or attendee.get("self"):
            continue
        if str(attendee.get("email") or "").strip().lower() not in team:
            return True
    return False


def _team_addresses(session: Session, team_id: str) -> set[str]:
    return {
        email.strip().lower()
        for email in session.scalars(
            select(User.email)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team_id)
        )
        if email
    }


def update_agenda(
    session: Session,
    gaps: Sequence[GapGap],
    *,
    team_id: str,
    user_id: str,
    carried: bool,
    event_id: str | None = None,
    now: datetime | None = None,
) -> AgendaOutcome:
    """Add the gaps' lines to an event on this person's calendar, or take them out.

    ``event_id`` is the event the person picked ("다음 회의 잡기"). Without one,
    the event is the team's next scheduled meeting's, found by its start time.
    Adding to an event shared outside the team is refused; taking lines out
    never is. What was written is recorded in ``GapAgendaEvent`` with the
    caller's own session, for the caller to commit.
    """
    starts: datetime | None = None
    if event_id is None:
        meeting = next_meeting(session, team_id, now=now or datetime.now(UTC))
        if meeting is None or meeting.started_at is None:
            return "no_next_meeting"
        # SQLite hands back a naive datetime; PostgreSQL's is already UTC-aware.
        starts = meeting.started_at
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=UTC)
    try:
        with calendar_of(session, user_id) as calendar:
            if calendar is None:
                return "not_connected"
            client, calendar_id = calendar
            if event_id is None and starts is not None:
                event_id = _event_starting(client, calendar_id, starts)
            if event_id is None:
                return "no_event"
            path = f"/calendars/{calendar_id}/events/{event_id}"
            current = client.request("GET", path, params={"fields": WRITE_FIELDS})
            if carried and outside_team(
                list(current.get("attendees") or []), _team_addresses(session, team_id)
            ):
                log.info("gap_agenda_refused_external", gaps=len(gaps))
                return "external_attendees"
            updated = edited(str(current.get("description") or ""), gaps, carried=carried)
            if updated is not None:
                client.request(
                    "PATCH",
                    path,
                    params={"sendUpdates": "all" if carried else "none"},
                    json={"description": updated},
                )
            written_to = (calendar_id, event_id)
    except ReconnectRequiredError:
        return "reconnect_required"
    except PermanentIntegrationError as exc:
        if exc.details.get("upstream_status") in (404, 410):
            return "no_event"
        log.warning("gap_agenda_failed", error=type(exc).__name__)
        return "failed"
    except (IntegrationError, PrivacyViolationError) as exc:
        log.warning("gap_agenda_failed", error=type(exc).__name__)
        return "failed"
    _record(
        session,
        gaps,
        user_id=user_id,
        calendar_id=written_to[0],
        event_id=written_to[1],
        kept=carried,
    )
    log.info("gap_agenda_set", gaps=len(gaps), carried=carried, picked=starts is None)
    return "added" if carried else "removed"


def written_gap_ids(session: Session, *, user_id: str, event_id: str | None) -> set[str]:
    """The gaps whose line this person already wrote onto this event -- or onto
    any event of theirs, when none is named. What "다음 회의 잡기" leaves out of
    the team's notice, so pressing again does not announce it again."""
    query = select(GapAgendaEvent.gap_id).where(GapAgendaEvent.user_id == user_id)
    if event_id is not None:
        query = query.where(GapAgendaEvent.event_id == event_id)
    return set(session.scalars(query))


def _insert(session: Session, model: Any) -> Any:
    dialect = session.get_bind().dialect.name
    return (postgresql.insert if dialect == "postgresql" else sqlite.insert)(model)


def _record(
    session: Session,
    gaps: Sequence[GapGap],
    *,
    user_id: str,
    calendar_id: str,
    event_id: str,
    kept: bool,
) -> None:
    """Remember which event holds each gap's line, or forget it once taken out."""
    if not gaps:
        return
    if not kept:
        session.execute(
            delete(GapAgendaEvent).where(
                GapAgendaEvent.gap_id.in_([gap.id for gap in gaps]),
                GapAgendaEvent.user_id == user_id,
                GapAgendaEvent.event_id == event_id,
            )
        )
        return
    session.execute(
        _insert(session, GapAgendaEvent)
        .values(
            [
                {
                    "meeting_id": gap.meeting_id,
                    "gap_id": gap.id,
                    "user_id": user_id,
                    "calendar_id": calendar_id,
                    "event_id": event_id,
                }
                for gap in gaps
            ]
        )
        .on_conflict_do_nothing(index_elements=["gap_id", "user_id", "event_id"])
    )


def _event_starting(client: CalendarClient, calendar_id: str, starts: datetime) -> str | None:
    events = _timed_events(client, calendar_id, starts - MATCH_WINDOW, starts + MATCH_WINDOW, 10)
    return next(
        (
            e.id
            for e in events
            if isinstance(e.start, datetime) and abs(e.start - starts) <= MATCH_WINDOW
        ),
        None,
    )


def _when(raw: dict[str, Any] | None) -> datetime | date | None:
    if not raw:
        return None
    if "dateTime" in raw:
        return datetime.fromisoformat(raw["dateTime"])
    if "date" in raw:
        return date.fromisoformat(raw["date"])
    return None


def _timed_events(
    client: CalendarClient, calendar_id: str, start: datetime, end: datetime, limit: int
) -> list[CalendarEvent]:
    """Events between two instants, recurring ones expanded, asked of Google
    for ``LIST_FIELDS`` only. ``CalendarClient.list_events`` asks for whole
    events, so it is not used here (#877)."""
    body = client.request(
        "GET",
        f"/calendars/{calendar_id}/events",
        params={
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": str(limit),
            "fields": LIST_FIELDS,
        },
    )
    return [
        CalendarEvent(
            id=str(raw["id"]),
            summary=str(raw.get("summary", "")),
            start=_when(raw.get("start")),
            end=_when(raw.get("end")),
        )
        for raw in body.get("items", [])
        if raw.get("status") != "cancelled"
    ]


EventsOutcome = Literal["ok", "not_connected", "reconnect_required", "failed"]

PICK_DAYS = 14
"""How far ahead "다음 회의 잡기" lists the person's events."""

PICK_LIMIT = 50
"""How many events the picker lists at most."""


def upcoming_events(
    session: Session, user_id: str, *, now: datetime | None = None
) -> tuple[EventsOutcome, list[CalendarEvent]]:
    """This person's own timed events over the next ``PICK_DAYS``, for them to
    pick the next meeting from. Read for the person asking and returned to
    them only; nothing is stored or logged but the count."""
    start = now or datetime.now(UTC)
    try:
        with calendar_of(session, user_id) as calendar:
            if calendar is None:
                return "not_connected", []
            client, calendar_id = calendar
            events = _timed_events(
                client, calendar_id, start, start + timedelta(days=PICK_DAYS), PICK_LIMIT
            )
    except ReconnectRequiredError:
        return "reconnect_required", []
    except IntegrationError as exc:
        log.warning("gap_agenda_events_failed", error=type(exc).__name__)
        return "failed", []
    timed = [e for e in events if isinstance(e.start, datetime)]
    log.info("gap_agenda_events_read", events=len(timed))
    return "ok", timed


# --- taking lines out when the meeting or the account goes -----------------------


def _strip(client: CalendarClient, calendar_id: str, event_id: str, gap_ids: Sequence[str]) -> None:
    """Take these gaps' lines out of one event. An event already gone is done.
    Raises ``IntegrationError`` or ``PrivacyViolationError`` when it cannot."""
    path = f"/calendars/{calendar_id}/events/{event_id}"
    try:
        current = client.request("GET", path, params={"fields": DESCRIPTION_FIELDS})
        updated = without_lines(str(current.get("description") or ""), gap_ids)
        if updated is not None:
            client.request("PATCH", path, json={"description": updated})
    except PermanentIntegrationError as exc:
        if exc.details.get("upstream_status") in (404, 410):
            return
        raise


def _client_configured() -> bool:
    """Whether this deployment can refresh anyone's Google grant at all."""
    return all(get_core_settings().google_integration_credentials)


@on_user_deleted("gap")
def forget_user_agenda_lines(user_id: str) -> None:
    """Before an account goes (privacy.md section 4): every line C wrote on
    that person's own calendar, taken out now -- with their grant, which goes
    with the account right after this hook. Lines queued for them by a
    meeting's expiry go too, for the same reason.

    Never raises: an expired token or an unreachable Google is logged and the
    deletion goes on, and the ``users`` cascade takes the records either way.
    Safe to run twice. Ids and counts only in the log."""
    try:
        with session_scope() as session:
            written = list(
                session.scalars(select(GapAgendaEvent).where(GapAgendaEvent.user_id == user_id))
            )
            queued = list(
                session.scalars(select(GapAgendaCleanup).where(GapAgendaCleanup.user_id == user_id))
            )
            events: dict[tuple[str, str], list[str]] = defaultdict(list)
            for line in written:
                events[(line.calendar_id, line.event_id)].append(line.gap_id)
            for waiting in queued:
                events[(waiting.calendar_id, waiting.event_id)].append(waiting.gap_id)
            removed = failed = 0
            if events:
                try:
                    with calendar_of(session, user_id) as calendar:
                        if calendar is None:
                            failed = len(events)
                        else:
                            client, _ = calendar
                            for (calendar_id, event_id), gap_ids in events.items():
                                try:
                                    _strip(client, calendar_id, event_id, gap_ids)
                                    removed += 1
                                except (IntegrationError, PrivacyViolationError):
                                    failed += 1
                except IntegrationError:
                    failed = len(events)
            for gone in (*written, *queued):
                session.delete(gone)
        if failed:
            # Best effort (privacy.md section 4): the account goes on, and these
            # lines stay on that calendar. Loud, because nothing can retry.
            log.warning(
                "gap_user_agenda_lines_left",
                user_id=user_id,
                failed=failed,
                client_configured=_client_configured(),
            )
        log.info("gap_user_agenda_lines_removed", user_id=user_id, removed=removed, failed=failed)
    except Exception as exc:  # noqa: BLE001 -- an account deletion must not stop on this
        log.warning("gap_user_agenda_lines_failed", user_id=user_id, error=type(exc).__name__)


@on_meeting_deleted("gap")
def queue_meeting_agenda_lines(meeting_id: str) -> None:
    """Before a meeting goes (privacy.md section 4): the lines its gaps left on
    people's calendars, copied into ``GapAgendaCleanup`` for
    ``drain_agenda_cleanup``.

    Only a copy -- no call to Google here, so a slow calendar never holds up
    the retention sweep. A database error is raised on purpose: the sweep then
    keeps the meeting and tries again, rather than deleting it with its lines
    unrecorded. Safe to run twice (the queue is unique per owner, event and
    gap)."""
    with session_scope() as session:
        rows = session.execute(
            select(
                GapAgendaEvent.user_id,
                GapAgendaEvent.calendar_id,
                GapAgendaEvent.event_id,
                GapAgendaEvent.gap_id,
            ).where(GapAgendaEvent.meeting_id == meeting_id)
        ).all()
        if rows:
            session.execute(
                _insert(session, GapAgendaCleanup)
                .values(
                    [
                        {"user_id": u, "calendar_id": c, "event_id": e, "gap_id": g}
                        for u, c, e, g in rows
                    ]
                )
                .on_conflict_do_nothing(index_elements=["user_id", "event_id", "gap_id"])
            )
    log.info("gap_meeting_agenda_lines_queued", meeting_id=meeting_id, lines=len(rows))


def queue_gap_lines(session: Session, gap_ids: Sequence[str]) -> int:
    """Queue these gaps' lines for ``drain_agenda_cleanup`` and forget where
    they were, in the caller's session; returns how many were queued.

    For a gap whose question named words a person has since deleted (#587):
    the line on the calendar quotes them, so it comes out rather than staying
    until the meeting goes. The gap's mark stays; pressing again writes the
    question it has now."""
    if not gap_ids:
        return 0
    rows = session.execute(
        select(
            GapAgendaEvent.user_id,
            GapAgendaEvent.calendar_id,
            GapAgendaEvent.event_id,
            GapAgendaEvent.gap_id,
        ).where(GapAgendaEvent.gap_id.in_(gap_ids))
    ).all()
    if not rows:
        return 0
    session.execute(
        _insert(session, GapAgendaCleanup)
        .values(
            [{"user_id": u, "calendar_id": c, "event_id": e, "gap_id": g} for u, c, e, g in rows]
        )
        .on_conflict_do_nothing(index_elements=["user_id", "event_id", "gap_id"])
    )
    session.execute(delete(GapAgendaEvent).where(GapAgendaEvent.gap_id.in_(gap_ids)))
    return len(rows)


def drain_agenda_cleanup() -> int:
    """Take queued lines off their owners' calendars, each with its owner's own
    grant. Returns how many events were cleaned.

    Taken out, or the event already gone: the rows go. A transient failure
    keeps them for the next run, up to ``CLEANUP_MAX_ATTEMPTS``. Nothing can
    take them out -- the owner disconnected their calendar, the grant is
    refused, or the event's description is one the outbound check refuses to
    send back -- and the rows go too, logged: keeping them would retry for
    good."""
    if not _client_configured():
        # Without the deployment's Google client no grant can be refreshed, and
        # every row would read as "no grant" and be dropped. Keep them.
        log.warning("gap_agenda_cleanup_no_client")
        return 0
    cleaned = 0
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(GapAgendaCleanup).order_by(GapAgendaCleanup.id).limit(CLEANUP_BATCH)
            )
        )
        by_owner: dict[str, dict[tuple[str, str], list[GapAgendaCleanup]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for row in rows:
            by_owner[row.user_id][(row.calendar_id, row.event_id)].append(row)
        for user_id, events in by_owner.items():
            try:
                with calendar_of(session, user_id) as calendar:
                    if calendar is None:
                        log.info("gap_agenda_cleanup_no_grant", user_id=user_id)
                        _drop(session, events.values())
                        continue
                    client, _ = calendar
                    for (calendar_id, event_id), queued in events.items():
                        try:
                            _strip(client, calendar_id, event_id, [q.gap_id for q in queued])
                        except PrivacyViolationError:
                            log.warning("gap_agenda_cleanup_refused", user_id=user_id)
                            _drop(session, [queued])
                            continue
                        except IntegrationError as exc:
                            _retry_or_drop(session, queued, user_id, exc)
                            continue
                        _drop(session, [queued])
                        cleaned += 1
            except ReconnectRequiredError:
                log.info("gap_agenda_cleanup_reconnect", user_id=user_id)
                _drop(session, events.values())
            except IntegrationError as exc:
                for queued in events.values():
                    _retry_or_drop(session, queued, user_id, exc)
    if rows:
        log.info("gap_agenda_cleanup_drained", queued=len(rows), cleaned=cleaned)
    return cleaned


def _drop(session: Session, groups: Any) -> None:
    for queued in groups:
        for row in queued:
            session.delete(row)


def _retry_or_drop(
    session: Session, queued: Sequence[GapAgendaCleanup], user_id: str, exc: Exception
) -> None:
    for row in queued:
        row.attempts += 1
        if row.attempts >= CLEANUP_MAX_ATTEMPTS:
            log.warning("gap_agenda_cleanup_gave_up", user_id=user_id, error=type(exc).__name__)
            session.delete(row)
