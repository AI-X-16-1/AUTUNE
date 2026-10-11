"""What a team's copies outside Autune did lately, for S28's "동기화 기록".

Read from rows module B already keeps. Nothing is recorded for this screen:

- ``ext_sync_failures`` -- the failure standing for an item and a system, its
  kind and its time (#680). What a card on the board says, gathered for a team.
- ``ext_external_refs`` -- the Notion page or Jira issue an item became, and
  when it was made.
- ``ext_calendar_events`` -- the event an item's due date became on one
  person's calendar, and when it was made.

**It is not a log of every attempt.** A failure is gone once a later attempt
goes through, and a copy's time is when the copy was first made, not when it
was last brought up to date. Keeping every attempt would be a table of what
people and tools did, with a retention rule of its own; none exists.

**Who is told what is ``sync_state``'s rule, unchanged.** Notion and Jira are
the team's connections, so their rows go to any member. A calendar is one
person's: a failed calendar copy goes to the item's assignee and to nobody
else (``sync_state.failures_for``), and an event that was made goes to the
person whose calendar holds it.

A meeting past its retention window shows nothing here, as on the board
(``service.within_retention``, #656).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_core import Meeting

from . import sync_state
from .models import ExtActionItem, ExtCalendarEvent, ExtExternalRef, ExtSyncFailure
from .schemas import SyncLogCopy, SyncLogFailure, SyncLogRead
from .service import within_retention

LIMIT = 30
"""How many rows of each kind the drawer gets, newest first. It is a look at
what happened lately, not an archive: the board holds every item."""


def team_sync_log(
    session: Session, *, team_id: str, reader_id: str, limit: int = LIMIT
) -> SyncLogRead:
    """The team's standing failures and its latest copies, as ``reader_id`` may
    see them. The caller has checked that the reader is on the team."""
    failed = session.scalars(
        select(ExtActionItem)
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .where(
            Meeting.team_id == team_id,
            within_retention(),
            ExtActionItem.id.in_(select(ExtSyncFailure.action_item_id)),
        )
    ).all()
    standing = sync_state.failures_for(session, failed, reader_id=reader_id)
    items = {item.id: item for item in failed}
    failures = sorted(
        ((items[item_id], failure) for item_id, rows in standing.items() for failure in rows),
        key=lambda pair: (pair[1].failed_at, pair[0].id, pair[1].system),
        reverse=True,
    )[:limit]

    refs = session.execute(
        select(ExtExternalRef, ExtActionItem)
        .join(ExtActionItem, ExtActionItem.id == ExtExternalRef.action_item_id)
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .where(
            Meeting.team_id == team_id,
            within_retention(),
            # A claim with no page yet is a send in flight, not a copy.
            ExtExternalRef.external_id.is_not(None),
        )
        .order_by(ExtExternalRef.created_at.desc())
        .limit(limit)
    ).tuples()
    events = session.execute(
        select(ExtCalendarEvent, ExtActionItem)
        .join(ExtActionItem, ExtActionItem.id == ExtCalendarEvent.action_item_id)
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .where(
            Meeting.team_id == team_id,
            within_retention(),
            ExtCalendarEvent.event_id.is_not(None),
            # One person's calendar: theirs to be told about, nobody else's.
            ExtCalendarEvent.user_id == reader_id,
        )
        .order_by(ExtCalendarEvent.created_at.desc())
        .limit(limit)
    ).tuples()
    copied = sorted(
        [(item, ref.system, ref.url, ref.created_at) for ref, item in refs]
        + [(item, sync_state.CALENDAR, None, event.created_at) for event, item in events],
        key=lambda row: (row[3], row[0].id, row[1]),
        reverse=True,
    )[:limit]

    meetings = {item.meeting_id for item, _ in failures} | {row[0].meeting_id for row in copied}
    titles: dict[str, str] = {}
    if meetings:
        titles = dict(
            session.execute(select(Meeting.id, Meeting.title).where(Meeting.id.in_(meetings)))
            .tuples()
            .all()
        )
    return SyncLogRead(
        failures=[
            SyncLogFailure(
                action_item_id=item.id,
                meeting_id=item.meeting_id,
                meeting_title=titles[item.meeting_id],
                description=item.description,
                system=failure.system,
                kind=failure.kind,
                failed_at=failure.failed_at,
            )
            for item, failure in failures
        ],
        copies=[
            SyncLogCopy(
                action_item_id=item.id,
                meeting_id=item.meeting_id,
                meeting_title=titles[item.meeting_id],
                description=item.description,
                system=system,  # type: ignore[arg-type]
                url=url,
                copied_at=copied_at,
            )
            for item, system, url, copied_at in copied
        ],
    )
