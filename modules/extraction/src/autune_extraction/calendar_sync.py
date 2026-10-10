"""Each person's own confirmed due dates on their own Google Calendar, both ways (#435).

**Out:** when a person confirms an item with a due date, its **assignee's** own
calendar -- connected through their own grant in ``user_integrations`` -- gets
one all-day event on that date. Later edits move, retitle or remove the same
event; reassigning the item moves it to the new assignee's calendar. Team work
is not copied into anybody's calendar: it stays on the S17 board.

**Back:** a person who drags their own task's event to another day has moved
the due date. ``pull_calendar_changes`` reads that back through the same edit
path the board uses (``service.update_action_item``), so the edit is recorded
and Notion follows. Only the assignee's own calendar is read, and only
Autune's own events in it: every event Autune makes carries a private tag
(``extendedProperties.private``) and the read asks Google for that tag, so the
rest of the calendar never comes back.

**What leaves** is the item's description (masked, as for Notion) in the title
and a fixed line saying where it came from -- or, for an item with a short
title (the owner, 2026-10-09; ``top_line``), that title in the event's title
and the description above the fixed line in the event's body. Either way the
title says it is something to do ("[할 일] "). Not the transcript, the source
utterances, the meeting title or anyone else's name -- it is the person's own
calendar, so it does not name them either. No attendees, so nobody is invited.

**Nothing before confirmation** (#246), and nothing for an item whose assignee
is not an identified team member or has not connected a calendar.

Rules, each tested:

- A date Autune wrote is never read back as the person's edit:
  ``ext_calendar_events.synced_due_date`` is what a read compares against.
- An event the person deletes in Calendar is let go (the row is dropped); the
  next edit in Autune makes it again.
- Reassigning takes the event off the old calendar at the item's next sync,
  which the reassigning edit itself starts. An assignee leaving the team
  starts no sync -- nothing tells this module -- so a sweep looks for their
  events every ten minutes and takes them off (``events_of_departed_owners``,
  ``take_back_from_departed_owner``,
  ``tasks.take_back_departed_calendar_events``). The sweep deletes and never
  writes an event.
  That cleanup is best effort: a previous assignee whose grant is gone (revoked,
  expired, or they left and never reconnect) must not keep the item off its new
  assignee's calendar (PARKJAEKYUNG0525, mminjae97, review of #441).
- Deleting the item in Autune deletes its event first (``remove_event``).
- A finished item keeps its event, titled ``[완료]``. One closed without being
  finished keeps it too, titled ``[닫힘]``: the calendar is the assignee's own,
  and it must not tell them they finished what was closed (#856).
- A due date removed in Autune removes the event.
- An event dragged onto a timed slot is due on that moment's date in Korea
  (``_due_day``), whatever zone the person's calendar answers in.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, TeamMember, get_logger
from autune_integrations import CalendarEvent, IntegrationError

from . import service, top_line
from .models import ExtActionItem, ExtCalendarCleanup, ExtCalendarEvent, ExtCalendarPoll
from .schemas import ActionItemUpdate
from .service import _insert_if_absent_into
from .slots import KST

log = get_logger(__name__)

CALENDAR = "calendar"
"""The ``user_integrations`` service a person's calendar grant is stored under."""

TAG = ("autune", "1")
"""The private property every Autune event carries, and the read-back's filter."""

ITEM_KEY = "autune_item"
"""The private property naming which item an event is for."""

EVENT_DESCRIPTION = "Autune에서 확정된 할 일의 마감일입니다."


class CalendarEvents(Protocol):
    """What this module calls -- ``CalendarClient`` and ``FakeCalendar`` both."""

    def create_all_day_event(
        self,
        calendar_id: str,
        summary: str,
        day: date,
        *,
        description: str = "",
        private: dict[str, str] | None = None,
    ) -> str: ...

    def update_all_day_event(
        self, calendar_id: str, event_id: str, summary: str, day: date, *, description: str = ""
    ) -> bool: ...

    def delete_event(self, calendar_id: str, event_id: str) -> None: ...

    def changed_events(
        self, calendar_id: str, *, updated_min: datetime, tag: tuple[str, str], max_pages: int = 10
    ) -> list[CalendarEvent]: ...


CalendarFor = Callable[[str], "tuple[CalendarEvents, str] | None"]
"""A person's calendar client and calendar id, or ``None`` when they have not
connected one. Built by the task from ``user_integrations``."""


def event_summary(item: ExtActionItem, *, closed: bool = False) -> str:
    """The event's title. ``closed`` is ``service.closed_unfinished``'s answer
    for the item: done, and not finished."""
    line = top_line.outbound_line("calendar", "item", item.title, item.description)
    if item.status != ActionStatus.DONE.value:
        return f"[마감] {line}"
    return f"{'[닫힘]' if closed else '[완료]'} {line}"


def event_description(item: ExtActionItem) -> str:
    """The event's body: the fixed line, under the item's sentence when the
    short title took the sentence's place in the event's title."""
    if top_line.titled(item.title, item.description):
        return f"{item.description}\n\n{EVENT_DESCRIPTION}"
    return EVENT_DESCRIPTION


def _calendar_owner(session: Session, item: ExtActionItem | None) -> str | None:
    """Whose calendar the item belongs on now, or ``None`` for nobody's: not
    confirmed, no date, no identified assignee, or an assignee no longer on the
    meeting's team (ADR 0007 -- a departed person's calendar gets no team work)."""
    if item is None or item.status == ActionStatus.NEEDS_CONFIRMATION.value:
        return None
    if item.due_date is None or not item.assignee_id:
        return None
    meeting = session.get(Meeting, item.meeting_id)
    if meeting is None:
        return None
    member = session.scalar(
        select(TeamMember.user_id).where(
            TeamMember.team_id == meeting.team_id, TeamMember.user_id == item.assignee_id
        )
    )
    return item.assignee_id if member is not None else None


def events_of_departed_owners(session: Session) -> list[str]:
    """Ids of the items whose event sits on the calendar of somebody who is no
    longer on the meeting's team.

    ``sync_due_date_to_calendar`` already takes such an event off -- at the
    item's next sync, which may never come: nothing tells this module that a
    person left a team (#552), and an item nobody edits is not synced again.
    This is what a sweep reads, to take each one off itself
    (``take_back_from_departed_owner``). The title of the event is the item's
    description, on the calendar of a person who can no longer open the item.

    The test is the one ``_calendar_owner`` makes, read the other way: a
    membership of **the meeting's own team**. Somebody on another team of the
    same deployment is not on this one; somebody still on it is never listed,
    whoever the item is assigned to now -- a reassignment's event moves with
    the edit that reassigned it, as before.
    """
    on_the_team = (
        select(TeamMember.id)
        .where(
            TeamMember.team_id == Meeting.team_id,
            TeamMember.user_id == ExtCalendarEvent.user_id,
        )
        .exists()
    )
    return list(
        session.scalars(
            select(ExtCalendarEvent.action_item_id)
            .join(Meeting, Meeting.id == ExtCalendarEvent.meeting_id)
            .where(~on_the_team)
            .order_by(ExtCalendarEvent.action_item_id)
        )
    )


def _queue_for_cleanup(session: Session, row: ExtCalendarEvent) -> None:
    """Hand an event Google would not delete just now to
    ``ext_calendar_cleanup``, where ``tasks.drain_calendar_cleanup`` tries
    again with its owner's grant (#672). The row it came from is about to go
    -- with the item, or to make room for the next assignee's event -- and
    after that nothing else knows the event's id. Its title is the item's
    description, so an event left behind is a sentence left behind."""
    if not row.event_id:
        return
    session.execute(
        _insert_if_absent_into(session, ExtCalendarCleanup)
        .values(user_id=row.user_id, event_id=row.event_id)
        .on_conflict_do_nothing(index_elements=["user_id", "event_id"])
    )


def _take_off(session: Session, calendar_for: CalendarFor, row: ExtCalendarEvent) -> None:
    """Take ``row``'s event off the calendar it is on, if that calendar can be
    reached now -- and queue it for another try if not (#672). Either way the
    row goes and the item moves on. A delete only: nothing of the item is sent."""
    action_item_id = row.action_item_id
    try:
        previous = calendar_for(row.user_id)
        if previous is not None and row.event_id:
            client, calendar_id = previous
            client.delete_event(calendar_id, row.event_id)
    except IntegrationError:
        log.warning("extraction_calendar_previous_unreachable", action_item_id=action_item_id)
        _queue_for_cleanup(session, row)
    session.delete(row)
    session.flush()
    log.info("extraction_calendar_removed", action_item_id=action_item_id)


def take_back_from_departed_owner(
    session: Session, calendar_for: CalendarFor, *, action_item_id: str
) -> bool:
    """Take the item's event off the calendar of an owner who is no longer on
    the meeting's team. Whether one was taken.

    **This only ever deletes.** It is not the item's sync: an item that was
    given to somebody else in the meantime gets no event on their calendar
    from here -- that is its own sync's to write, when the item is next edited
    or synced, as it would have been had nobody left. A sweep that also wrote
    would put an event on a member's calendar minutes after somebody else left
    the team, with nothing the member did to cause it.

    The membership is read again under the row's lock: a person who came back
    between the sweep's list and this call keeps their event.
    """
    row = session.get(ExtCalendarEvent, action_item_id, with_for_update=True)
    if row is None:
        return False
    still_on_the_team = session.scalar(
        select(TeamMember.id)
        .join(Meeting, Meeting.team_id == TeamMember.team_id)
        .where(Meeting.id == row.meeting_id, TeamMember.user_id == row.user_id)
    )
    if still_on_the_team is not None:
        return False
    _take_off(session, calendar_for, row)
    return True


def sync_due_date_to_calendar(
    session: Session, calendar_for: CalendarFor, *, action_item_id: str
) -> ExtCalendarEvent | None:
    """Make the item's event match the item as it is now, on the right person's
    calendar. ``None`` when the item has no event and needs none.

    The row is locked before the item is read (``populate_existing``), for the
    reason ``sync_action_item_to_notion`` gives: of two edits in flight, the one
    sending last must be the one that read last. The first event is claimed
    before it is created, so a confirmation delivered twice makes one.
    """
    row = session.get(ExtCalendarEvent, action_item_id, with_for_update=True)
    item = session.get(ExtActionItem, action_item_id, populate_existing=True)
    owner = _calendar_owner(session, item)

    if row is not None and row.user_id != owner:
        # Reassigned, undated, moved back, or its assignee left.
        _take_off(session, calendar_for, row)
        row = None

    if owner is None or item is None or item.due_date is None:
        return None
    connection = calendar_for(owner)
    if connection is None:
        return None  # the assignee has not connected a calendar
    client, calendar_id = connection

    if row is None:
        claimed = session.scalars(
            _insert_if_absent_into(session, ExtCalendarEvent)
            .values(
                action_item_id=item.id,
                meeting_id=item.meeting_id,
                user_id=owner,
                synced_due_date=item.due_date,
            )
            .on_conflict_do_nothing(index_elements=["action_item_id"])
            .returning(ExtCalendarEvent.action_item_id)
        ).one_or_none()
        row = session.get(
            ExtCalendarEvent, item.id, with_for_update=claimed is None, populate_existing=True
        )
        assert row is not None

    summary = event_summary(
        item,
        closed=item.status == ActionStatus.DONE.value
        and item.id in service.closed_unfinished(session, [item.id]),
    )
    if row.event_id and client.update_all_day_event(
        calendar_id, row.event_id, summary, item.due_date, description=event_description(item)
    ):
        row.synced_due_date = item.due_date
        log.info("extraction_calendar_updated", action_item_id=item.id)
        return row
    # First event, or one deleted by hand since: make it.
    row.event_id = client.create_all_day_event(
        calendar_id,
        summary,
        item.due_date,
        description=event_description(item),
        private={TAG[0]: TAG[1], ITEM_KEY: item.id},
    )
    row.synced_due_date = item.due_date
    log.info("extraction_calendar_created", action_item_id=item.id)
    return row


def _due_day(start: datetime | date | None) -> date | None:
    """The due date an event's start stands for.

    An all-day event is a date already and is that date: it has no moment and
    no zone, and it is what Autune writes itself.

    A timed one -- the person dragged the event onto an hour -- is a moment,
    and its due date is **that moment's date in Korea** (``slots.KST``), as
    every other date in B is read: there is no team time zone. Google answers
    in the calendar's own zone, so ``start.date()`` gave the day before for a
    calendar set west of Korea: 01:00 on the 14th in Korea came back as 16:00
    on the 13th from a calendar on UTC, and the item was due the 13th while
    the board and the reminders counted by the 14th.

    A time with no offset is not a moment and is read as the date written.
    ``astimezone`` would take it for the server's local time -- UTC on a
    server -- and move a late-evening slot to the next day.
    """
    if not isinstance(start, datetime):
        return start
    if start.utcoffset() is None:
        return start.date()
    return start.astimezone(KST).date()


def pull_calendar_changes(
    session: Session,
    client: CalendarEvents,
    *,
    user_id: str,
    calendar_id: str,
    since: datetime,
) -> list[str]:
    """Read back what ``user_id`` changed on their own calendar since ``since``:
    a moved date becomes the item's due date, a deleted event is let go. Returns
    the ids of items whose due date moved, for the caller to sync onward.

    Only this person's own items count -- an event whose row names someone else,
    or whose item was reassigned since, is ignored.
    """
    moved: list[str] = []
    for event in client.changed_events(calendar_id, updated_min=since, tag=TAG):
        item_id = event.private.get(ITEM_KEY)
        if not item_id:
            continue
        row = session.get(ExtCalendarEvent, item_id, with_for_update=True)
        if row is None or row.user_id != user_id or row.event_id != event.id:
            continue
        if event.cancelled:
            session.delete(row)
            log.info("extraction_calendar_let_go", action_item_id=item_id)
            continue
        day = _due_day(event.start)
        if day is None or day == row.synced_due_date:
            continue
        item = session.get(ExtActionItem, item_id, populate_existing=True)
        if _calendar_owner(session, item) != user_id or item is None:
            continue
        service.update_action_item(session, item, ActionItemUpdate(due_date=day))
        row.synced_due_date = day
        moved.append(item_id)
        log.info("extraction_calendar_date_read_back", action_item_id=item_id)
    session.flush()
    return moved


def remove_event(session: Session, calendar_for: CalendarFor, *, action_item_id: str) -> None:
    """Take an item's event off its assignee's calendar before the item itself is
    deleted -- a person asking Autune to delete an item is asking for it to be
    gone (invariant 11), and once the row cascades away the event can no longer
    be found (PARKJAEKYUNG0525, review of #441). An unreachable calendar does
    not block the deletion: the event is queued for
    ``tasks.drain_calendar_cleanup`` to remove later (#672).
    """
    row = session.get(ExtCalendarEvent, action_item_id)
    if row is None or not row.event_id:
        return
    try:
        connection = calendar_for(row.user_id)
        if connection is not None:
            client, calendar_id = connection
            client.delete_event(calendar_id, row.event_id)
            log.info("extraction_calendar_removed_with_item", action_item_id=action_item_id)
    except IntegrationError:
        log.warning("extraction_calendar_previous_unreachable", action_item_id=action_item_id)
        _queue_for_cleanup(session, row)


def forget_calendar_cursor(session: Session, user_id: str) -> None:
    """Drop a person's read-back cursor -- on reconnecting, so the next read
    starts from the first-connection lookback rather than a stale time."""
    row = session.get(ExtCalendarPoll, user_id)
    if row is not None:
        session.delete(row)
