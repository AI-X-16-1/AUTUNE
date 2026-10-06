"""A person's own leave dates on their own Google Calendar, when they ask (2026-10-06).

The dates a person sets so that no morning DM and no Monday digest reach them
(``ext_notification_pauses``) stay theirs alone unless they tick "내 Google
캘린더에도 추가" beside them. Then, and only then, one all-day event over the
range goes onto **their own** calendar through **their own** grant
(``user_integrations``), and its id is kept on the pause.

**What leaves** is the two dates, the fixed title "휴가" and a fixed line saying
where it came from. Nothing about a meeting, an item or another person; no
attendees, so nobody is invited or told.

**Who can see it** is decided by the person's calendar, so the event is written
``visibility: private``: somebody the calendar is shared with sees that the
person is busy on those days, not why.

Rules, each tested:

- Nothing is written without the tick. A person who has not connected a
  calendar is told so and nothing is tried.
- A changed range moves the same event; unticking or clearing the dates
  removes it. An event the person deleted in Calendar is made again only by
  another tick.
- A removal Google did not answer is queued (``ext_calendar_cleanup``) and
  tried again with the person's own grant, like a due-date event's.
- After the last day the pause goes (``service.forget_ended_pauses``) and its
  event **stays** on the calendar: it is the person's own record of a leave
  they took, and Autune no longer knows its id.
- Autune never reads the calendar for it. A leave the person wrote there
  themselves is not looked for, and the out-of-office read
  (``days_off.away_now``) asks Google for out-of-office events only, which
  this plain event is not.
- No log line carries the dates.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.orm import Session

from autune_core import get_logger, load_user_integration
from autune_core.settings import get_settings as get_core_settings
from autune_integrations import (
    IntegrationError,
    PermanentIntegrationError,
    ReconnectRequiredError,
)

from . import calendar_sync, service
from .models import ExtCalendarCleanup, ExtNotificationPause

log = get_logger(__name__)

LEAVE_TAG = ("autune_leave", "1")
"""The private property on a leave event. Not ``calendar_sync.TAG``: the
due-date read-back asks Google for that one, and must not see these."""

EVENT_TITLE = "휴가"
EVENT_DESCRIPTION = "Autune의 휴가 기간 설정에서 추가한 일정입니다."

Outcome = Literal["off", "added", "removed", "removal_queued", "not_connected", "failed"]
"""What happened on the calendar for one save. ``off``: not asked for and
nothing was there. ``failed``: asked for and not written -- the dates are saved
all the same. ``removal_queued``: the event could not be removed now and will
be tried again."""


def connected(session: Session, user_id: str) -> bool:
    """Whether this person has a calendar grant Autune can use -- what the
    screen draws the box by. The same answer ``tasks._calendars`` would give
    without asking Google: no grant, a deployment with no Google client, a
    grant issued to another client and one known to be revoked are all "no",
    so a box is not offered that could only answer ``not_connected``."""
    config = load_user_integration(session, user_id, calendar_sync.CALENDAR)
    if config is None or not config.secret or config.config.get("grant_revoked"):
        return False
    client_id, client_secret = get_core_settings().google_integration_credentials
    if not client_id or not client_secret:
        return False
    issued_to = config.config.get("client_id")
    return not issued_to or issued_to == client_id


def _event_body(starts_on: date, ends_on: date) -> dict[str, Any]:
    # Google's all-day end date is exclusive: a leave through the 9th ends on the 10th.
    return {
        "summary": EVENT_TITLE,
        "description": EVENT_DESCRIPTION,
        "start": {"date": starts_on.isoformat()},
        "end": {"date": (ends_on + timedelta(days=1)).isoformat()},
        "visibility": "private",
        "transparency": "opaque",
    }


def _write_event(calendar: Any, calendar_id: str, pause: ExtNotificationPause, old: str) -> str:
    """The event written over ``old`` when it is still there; otherwise a new
    one, tagged. One deleted by hand -- 404, 410, or kept by Google as
    ``cancelled`` -- is replaced, never revived (as ``update_all_day_event``)."""
    body = _event_body(pause.starts_on, pause.ends_on)
    if old:
        try:
            answer: dict[str, Any] | None = calendar.request(
                "PATCH", f"/calendars/{calendar_id}/events/{old}", json=body
            )
        except PermanentIntegrationError as exc:
            if exc.details.get("upstream_status") not in (404, 410):
                raise
            answer = None
        if answer is not None and answer.get("status") != "cancelled":
            return old
    body["extendedProperties"] = {"private": {LEAVE_TAG[0]: LEAVE_TAG[1]}}
    made = calendar.request("POST", f"/calendars/{calendar_id}/events", json=body)
    return str(made.get("id", ""))


def _connection(
    calendar_for: calendar_sync.CalendarFor, user_id: str
) -> tuple[Any, str] | None | Literal["failed"]:
    """The person's calendar, ``None`` when they have none connected (or the
    grant has to be given again), ``"failed"`` when it could not be reached."""
    try:
        return calendar_for(user_id)
    except ReconnectRequiredError:
        # A refused or revoked grant reads as not connected: the person is sent
        # to the connect card, which is where that is put right.
        return None
    except Exception as exc:  # noqa: BLE001 -- the dates are saved either way
        log.warning("extraction_leave_calendar_unreachable", error=type(exc).__name__)
        return "failed"


def _remove(
    session: Session, calendar_for: calendar_sync.CalendarFor, user_id: str, event_id: str
) -> Outcome:
    """Take ``event_id`` off the person's calendar, or queue it when Google
    does not answer. A calendar no longer connected cannot be reached: the
    event stays where the person can delete it, and nothing is queued."""
    connection = _connection(calendar_for, user_id)
    if connection is None:
        log.info("extraction_leave_event_left", user_id=user_id, reason="not_connected")
        return "not_connected"
    if connection != "failed":
        client, calendar_id = connection
        try:
            client.delete_event(calendar_id, event_id)
            return "removed"
        except IntegrationError as exc:
            log.warning("extraction_leave_event_not_removed", error=type(exc).__name__)
    session.execute(
        service._insert_if_absent_into(session, ExtCalendarCleanup)
        .values(user_id=user_id, event_id=event_id)
        .on_conflict_do_nothing(index_elements=["user_id", "event_id"])
    )
    return "removal_queued"


def set_leave(
    session: Session,
    calendar_for: calendar_sync.CalendarFor,
    user_id: str,
    *,
    starts_on: date | None,
    ends_on: date | None,
    on_calendar: bool,
    now: datetime,
) -> Outcome:
    """Set, replace or clear ``user_id``'s own leave dates, and make their
    calendar follow when they asked for that. Returns what happened there.

    The dates are saved whatever the calendar does: a leave that could not be
    written to Google still stops the morning DM. The row is locked before the
    calendar is asked (``service.set_notification_pause``), so a double press
    moves one event rather than making two.
    """
    if starts_on is None and ends_on is None:
        standing = session.get(ExtNotificationPause, user_id, with_for_update=True)
        old = (standing.calendar_event_id or "") if standing is not None else ""
        service.set_notification_pause(session, user_id, starts_on=None, ends_on=None, now=now)
        return _remove(session, calendar_for, user_id, old) if old else "off"

    pause = service.set_notification_pause(
        session, user_id, starts_on=starts_on, ends_on=ends_on, now=now
    )
    if pause is None:
        return "off"
    old = pause.calendar_event_id or ""
    if not on_calendar:
        pause.calendar_event_id = None
        session.flush()
        return _remove(session, calendar_for, user_id, old) if old else "off"

    connection = _connection(calendar_for, user_id)
    if connection is None:
        # An event written before the grant went keeps its id: connecting again
        # and saving moves it rather than leaving the old range beside a new one.
        return "not_connected"
    if connection == "failed":
        return "failed"
    client, calendar_id = connection
    try:
        pause.calendar_event_id = _write_event(client, calendar_id, pause, old) or None
    except IntegrationError as exc:
        log.warning("extraction_leave_event_not_written", error=type(exc).__name__)
        return "failed"
    session.flush()
    return "added" if pause.calendar_event_id else "failed"
