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
- A save that does not say (``on_calendar`` left out -- a screen that drew no
  box) leaves the calendar as it stands: an event there moves with the dates
  and keeps its id, and where there is none, none is made. **It never makes
  one**: an event the person deleted in Calendar is made again only by a
  tick, so a save that does not say finds it gone, drops its id and answers
  ``off`` (lsh2217's and pr's note on #922).
- A changed range moves the same event; unticking or clearing the dates
  removes it. An event the person deleted in Calendar is made again only by
  another tick.
- A removal Google did not answer is queued (``ext_calendar_cleanup``) and
  tried again with the person's own grant, like a due-date event's. A removal
  from a calendar that is no longer connected cannot be tried at all, and the
  person is told to delete the event themselves (``not_removed``).
- **Google is asked with no transaction open** (mminjae97's review of #922).
  The dates are committed first, the calendar is asked holding neither the
  row's lock nor a connection, and what it answered is written in a second
  transaction. One save at a time is at the calendar for a person: the first
  leaves a claim on the row (``calendar_claimed_at``), and a save that finds a
  claim younger than ``CLAIM_FOR`` is refused whole -- so a double press still
  makes one event, never two.
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

from autune_core import User, get_logger, load_user_integration
from autune_core.errors import ConflictError
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

CLAIM_FOR = timedelta(minutes=2)
"""How long one save's claim on a person's calendar keeps another save out.
Past what a save can take -- a token refresh and two calls to Google, each cut
off by its client's ten-second timeouts and none retried, about half a minute
together -- so a live claim is a save still out there; and short,
because a claim left by a process that died is in the person's way until it
is this old."""

Outcome = Literal[
    "off", "added", "removed", "removal_queued", "not_connected", "not_removed", "failed"
]
"""What happened on the calendar for one save. ``off``: not asked for and
nothing was there -- or nothing is there any more: a save that did not say
found the event deleted in Calendar and did not make it again. ``failed``:
asked for and not written -- the dates are saved all the same.
``not_connected``: asked for, and there is no calendar to write to.
``removal_queued``: the event could not be removed now and will be tried
again. ``not_removed``: the event could not be removed and will not be tried
again -- the calendar is no longer connected, and the event stays until the
person deletes it."""


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


def _write_event(
    calendar: Any,
    calendar_id: str,
    starts_on: date,
    ends_on: date,
    old: str,
    *,
    may_create: bool = True,
) -> str | None:
    """The event written over ``old`` when it is still there; otherwise a new
    one, tagged. One deleted by hand -- 404, 410, or kept by Google as
    ``cancelled`` -- is replaced, never revived (as ``update_all_day_event``).

    With ``may_create`` false nothing is made: ``None`` says ``old`` is gone
    and no event stands in its place. Only a tick makes an event."""
    body = _event_body(starts_on, ends_on)
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
    if not may_create:
        return None
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


def _queue_removal(session: Session, user_id: str, event_id: str) -> None:
    session.execute(
        service._insert_if_absent_into(session, ExtCalendarCleanup)
        .values(user_id=user_id, event_id=event_id)
        .on_conflict_do_nothing(index_elements=["user_id", "event_id"])
    )


def _remove(
    session: Session, calendar_for: calendar_sync.CalendarFor, user_id: str, event_id: str
) -> Outcome:
    """Take ``event_id`` off the person's calendar, or queue it when Google
    does not answer. A calendar no longer connected cannot be reached: the
    event stays where the person can delete it, and nothing is queued.

    Called with nothing open: the id was taken off the pause, under its lock,
    in a transaction already committed, so this event is nobody else's to
    touch and Google is asked holding nothing. Only a queued removal writes,
    and the caller commits it."""
    connection = _connection(calendar_for, user_id)
    if connection is None:
        log.info("extraction_leave_event_left", user_id=user_id, reason="not_connected")
        return "not_removed"
    if connection != "failed":
        client, calendar_id = connection
        try:
            client.delete_event(calendar_id, event_id)
            return "removed"
        except IntegrationError as exc:
            log.warning("extraction_leave_event_not_removed", error=type(exc).__name__)
    _queue_removal(session, user_id, event_id)
    return "removal_queued"


def _refuse_while_claimed(
    session: Session, pause: ExtNotificationPause | None, now: datetime
) -> None:
    """Another save of this person's is at the calendar: this one changes
    nothing -- not the dates either, or the event being written would be for a
    range the row no longer has."""
    if pause is None or pause.calendar_claimed_at is None:
        return
    if now - service._aware(pause.calendar_claimed_at) < CLAIM_FOR:
        session.rollback()
        raise ConflictError("an earlier save of these dates is still being written; try again")


def _at_google(
    calendar_for: calendar_sync.CalendarFor,
    user_id: str,
    starts_on: date,
    ends_on: date,
    old: str,
    *,
    may_create: bool,
) -> tuple[Outcome, str]:
    """Ask the calendar for the event; what happened, and the id the pause
    should hold afterwards. Touches no row.

    ``may_create`` is whether the save carried the tick. Without it an event
    that stands is moved, and one that turns out to be gone -- deleted in
    Calendar by the person -- is not made again: ``off``, and no id."""
    connection = _connection(calendar_for, user_id)
    if connection is None:
        # An event written before the grant went keeps its id: connecting again
        # and saving moves it rather than leaving the old range beside a new one.
        return "not_connected", old
    if connection == "failed":
        return "failed", old
    client, calendar_id = connection
    try:
        written = _write_event(client, calendar_id, starts_on, ends_on, old, may_create=may_create)
    except IntegrationError as exc:
        log.warning("extraction_leave_event_not_written", error=type(exc).__name__)
        return "failed", old
    if written is None:
        return "off", ""
    return ("added" if written else "failed"), written


def _settle(
    session: Session, user_id: str, *, claim: datetime, old: str, outcome: Outcome, event_id: str
) -> Outcome:
    """The second transaction: what Google answered goes on the row, and the
    claim comes off it.

    Only while the claim is still this save's. One that outlived
    ``CLAIM_FOR`` may have been taken over by a later save, and the row may be
    gone with the pause's last day or the account: then the row is somebody
    else's to write, and an event this save made new is queued for removal
    rather than left on the calendar with nothing pointing at it."""
    pause = session.get(ExtNotificationPause, user_id, with_for_update=True, populate_existing=True)
    mine = (
        pause is not None
        and pause.calendar_claimed_at is not None
        and service._aware(pause.calendar_claimed_at) == claim
    )
    if pause is None or not mine:
        log.warning("extraction_leave_claim_lost", user_id=user_id)
        if event_id and event_id != old and session.get(User, user_id) is not None:
            _queue_removal(session, user_id, event_id)
        return "failed"
    pause.calendar_event_id = event_id or None
    pause.calendar_claimed_at = None
    session.flush()
    return outcome


def set_leave(
    session: Session,
    calendar_for: calendar_sync.CalendarFor,
    user_id: str,
    *,
    starts_on: date | None,
    ends_on: date | None,
    on_calendar: bool | None,
    now: datetime,
) -> Outcome:
    """Set, replace or clear ``user_id``'s own leave dates, and make their
    calendar follow when they asked for that. Returns what happened there.

    ``on_calendar`` is the tick, and ``None`` when the save did not say: then
    it is whatever stands -- an event already there is kept and moved, and
    nothing is made where there is none.

    The dates are saved whatever the calendar does: a leave that could not be
    written to Google still stops the morning DM.

    **This commits, and more than once.** Three steps: the dates, under the
    row's lock (``service.set_notification_pause``), committed; then Google,
    with no transaction open, so a slow calendar holds neither the row nor a
    connection; then what Google answered, in a transaction the caller
    commits. A double press moves one event rather than making two because the
    first save's claim refuses the second (``_refuse_while_claimed``,
    ``ConflictError``) -- it does not wait.
    """
    if starts_on is None and ends_on is None:
        standing = session.get(ExtNotificationPause, user_id, with_for_update=True)
        _refuse_while_claimed(session, standing, now)
        old = (standing.calendar_event_id or "") if standing is not None else ""
        service.set_notification_pause(session, user_id, starts_on=None, ends_on=None, now=now)
        session.commit()
        return _remove(session, calendar_for, user_id, old) if old else "off"

    pause = service.set_notification_pause(
        session, user_id, starts_on=starts_on, ends_on=ends_on, now=now
    )
    if pause is None or starts_on is None or ends_on is None:
        return "off"
    _refuse_while_claimed(session, pause, now)
    old = pause.calendar_event_id or ""
    # Only a save that says so may make an event; one that does not say may
    # move the one that stands.
    ticked = on_calendar is True
    if on_calendar is None:
        # Not said: as it stands. Read here, under the row's lock.
        on_calendar = bool(old)
    if not on_calendar:
        # Taken off the row here, under its lock: the removal below is then of
        # an event no other save knows.
        pause.calendar_event_id = None
        session.commit()
        return _remove(session, calendar_for, user_id, old) if old else "off"

    pause.calendar_claimed_at = now
    session.commit()
    try:
        outcome, event_id = _at_google(
            calendar_for, user_id, starts_on, ends_on, old, may_create=ticked
        )
    except BaseException:
        # Nothing was learned about the event: the id stays, the claim goes.
        _settle(session, user_id, claim=now, old=old, outcome="failed", event_id=old)
        session.commit()
        raise
    return _settle(session, user_id, claim=now, old=old, outcome=outcome, event_id=event_id)
