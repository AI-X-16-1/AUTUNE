"""What the board can say about an item's copies outside Autune (#680).

Until this, a card could say "sent" (a link) or "sending" (a claim with no
link yet) and nothing else. Two things it could not say:

**That a copy failed.** The claim in ``ext_external_refs`` is rolled back with
the failed call, so nothing was left to draw. ``ext_sync_failures`` keeps the
latest failure per item and system -- **its kind and its time, and nothing
else**. Not the outside service's message, which may echo what was sent; not
what was being sent. The next attempt that succeeds removes the row.

**That there was nothing to copy.** An item with no calendar event is usually
not a failure: it has no date, or no account behind its assignee, or is not
confirmed. ``calendar_state`` names the first thing missing, so somebody who
added an item by hand is not left asking why nothing appeared.

**Notion and Jira are the team's; a calendar is one person's.** What is missing
from the item is said to everybody. Anything that says whether the *assignee*
has connected a calendar -- an event being there, none being there, a failed
calendar copy -- is said to the assignee and to nobody else.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, PrivacyViolationError, TeamMember, users_with_integration
from autune_core.oauth.atlassian import JiraReconnectRequiredError
from autune_integrations import ReconnectRequiredError, TransientIntegrationError

from .models import ExtActionItem, ExtCalendarEvent, ExtExternalRef, ExtSyncFailure
from .schemas import CalendarState, SyncFailureRead

NOTION, JIRA, CALENDAR = "notion", "jira", "calendar"

PRIVACY, RECONNECT, UNREACHABLE, REJECTED = "privacy", "reconnect", "unreachable", "rejected"


def kind_of(exc: BaseException) -> str:
    """Which of the four kinds a failed copy was. The class of the error and
    nothing from it: an exception's message is treated as published
    (privacy.md section 6) and is never read here."""
    if isinstance(exc, PrivacyViolationError):
        return PRIVACY
    if isinstance(exc, (ReconnectRequiredError, JiraReconnectRequiredError)):
        return RECONNECT
    if isinstance(exc, TransientIntegrationError):
        return UNREACHABLE
    return REJECTED


def record_failure(
    session: Session,
    action_item_id: str,
    system: str,
    kind: str,
    *,
    now: datetime | None = None,
) -> None:
    """Keep that the latest copy of this item to ``system`` failed, replacing
    whatever was kept before. Nothing is written for an item that is gone."""
    if session.get(ExtActionItem, action_item_id) is None:
        return
    when = now or datetime.now(tz=UTC)
    # One upsert, not get-then-add: two syncs failing at once (an edit and a
    # retry) used to both add, and the second hit the primary key and was
    # lost (PARKJAEKYUNG0525, review of #754).
    insert = postgresql.insert if session.get_bind().dialect.name == "postgresql" else sqlite.insert
    statement = insert(ExtSyncFailure).values(
        action_item_id=action_item_id, system=system, kind=kind, failed_at=when
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["action_item_id", "system"],
            set_={"kind": statement.excluded.kind, "failed_at": statement.excluded.failed_at},
        )
    )
    session.flush()


def clear_failure(session: Session, action_item_id: str, system: str) -> None:
    """A copy went through, or there was nothing to copy: no failure stands."""
    session.execute(
        delete(ExtSyncFailure).where(
            ExtSyncFailure.action_item_id == action_item_id, ExtSyncFailure.system == system
        )
    )


def failures_for(
    session: Session, items: Collection[ExtActionItem], *, reader_id: str | None
) -> dict[str, list[SyncFailureRead]]:
    """The failures standing for each item that ``reader_id`` may be told
    about, in one query for the whole list.

    Notion and Jira are the team's connections, and a failure of either is the
    team's to see. **A calendar is one person's**: its failure -- a refused
    grant above all -- is a fact about that person's own account, so it is
    returned only when the reader is the item's assignee (review of this
    change). A caller with no reader gets none of them.

    **An item whose copies do not follow shows none** -- back in 확인 필요 with
    nothing outside, so no sync runs for it, "다시 시도" queues nothing, and a
    failure kept from its first confirmation would stay red for good
    (PARKJAEKYUNG0525, review of #754). The rule is ``service.copies_follow``'s,
    read here for the whole list at once.
    """
    if not items:
        return {}
    assignee = {item.id: item.assignee_id for item in items}
    drafts = [item.id for item in items if item.status == ActionStatus.NEEDS_CONFIRMATION.value]
    if drafts:
        copied = set(
            session.scalars(
                select(ExtExternalRef.action_item_id).where(
                    ExtExternalRef.action_item_id.in_(drafts)
                )
            )
        ) | set(
            session.scalars(
                select(ExtCalendarEvent.action_item_id).where(
                    ExtCalendarEvent.action_item_id.in_(drafts),
                    ExtCalendarEvent.event_id.is_not(None),
                )
            )
        )
        for item_id in drafts:
            if item_id not in copied:
                assignee.pop(item_id)
        if not assignee:
            return {}
    rows = session.scalars(
        select(ExtSyncFailure)
        .where(ExtSyncFailure.action_item_id.in_(assignee))
        .order_by(ExtSyncFailure.system)
    )
    by_item: dict[str, list[SyncFailureRead]] = {}
    for row in rows:
        mine = reader_id is not None and assignee[row.action_item_id] == reader_id
        if row.system == CALENDAR and not mine:
            continue
        by_item.setdefault(row.action_item_id, []).append(
            SyncFailureRead(system=row.system, kind=row.kind, failed_at=row.failed_at)  # type: ignore[arg-type]
        )
    return by_item


def calendar_state(
    session: Session, item: ExtActionItem, *, reader_id: str | None
) -> CalendarState | None:
    """What ``reader_id`` may be told about the item and its assignee's calendar.

    **What is missing from the item is said to everybody** -- it is not
    confirmed, has no date, has a typed name or nobody for an assignee, or an
    assignee who is not on the team. Those are facts about the item, on the
    board already, and they are the conditions ``calendar_sync._calendar_owner``
    applies, in its order.

    **Past those, the answer is about one person's calendar, and only that
    person gets one.** Whether an event is there, that none is, and that they
    have not connected a calendar each say whether somebody has connected
    theirs -- "it is on their calendar" as plainly as "they have not
    connected". A first version withheld only the last and told a teammate the
    rest, which gave the same fact away by elimination. So a reader who is not
    the assignee gets ``None``: nothing about the calendar at all.
    """
    if item.status == ActionStatus.NEEDS_CONFIRMATION.value:
        return CalendarState(state="none", reason="not_confirmed")
    if item.due_date is None:
        return CalendarState(state="none", reason="no_due_date")
    if not item.assignee_id:
        return CalendarState(state="none", reason="no_account")
    on_team = session.scalar(
        select(TeamMember.id)
        .join(Meeting, Meeting.team_id == TeamMember.team_id)
        .where(Meeting.id == item.meeting_id, TeamMember.user_id == item.assignee_id)
    )
    if on_team is None:
        return CalendarState(state="none", reason="not_on_team")
    if reader_id is None or reader_id != item.assignee_id:
        return None
    event = session.scalar(
        select(ExtCalendarEvent.event_id).where(
            ExtCalendarEvent.action_item_id == item.id, ExtCalendarEvent.event_id.is_not(None)
        )
    )
    if event:
        return CalendarState(state="sent")
    if item.assignee_id not in users_with_integration(session, CALENDAR):
        return CalendarState(state="none", reason="not_connected")
    return CalendarState(state="none")
