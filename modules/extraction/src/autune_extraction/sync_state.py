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
added an item by hand is not left asking why nothing appeared. Whether the
*assignee* has connected a calendar is said only to the assignee.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, PrivacyViolationError, TeamMember, users_with_integration
from autune_core.oauth.atlassian import JiraReconnectRequiredError
from autune_integrations import ReconnectRequiredError, TransientIntegrationError

from .models import ExtActionItem, ExtCalendarEvent, ExtSyncFailure
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
    row = session.get(ExtSyncFailure, (action_item_id, system))
    when = now or datetime.now(tz=UTC)
    if row is None:
        session.add(
            ExtSyncFailure(action_item_id=action_item_id, system=system, kind=kind, failed_at=when)
        )
    else:
        row.kind, row.failed_at = kind, when
    session.flush()


def clear_failure(session: Session, action_item_id: str, system: str) -> None:
    """A copy went through, or there was nothing to copy: no failure stands."""
    session.execute(
        delete(ExtSyncFailure).where(
            ExtSyncFailure.action_item_id == action_item_id, ExtSyncFailure.system == system
        )
    )


def failures_for(
    session: Session, action_item_ids: Collection[str]
) -> dict[str, list[SyncFailureRead]]:
    """The failures standing for each item, in one query for the whole list."""
    if not action_item_ids:
        return {}
    rows = session.scalars(
        select(ExtSyncFailure)
        .where(ExtSyncFailure.action_item_id.in_(action_item_ids))
        .order_by(ExtSyncFailure.system)
    )
    by_item: dict[str, list[SyncFailureRead]] = {}
    for row in rows:
        by_item.setdefault(row.action_item_id, []).append(
            SyncFailureRead(system=row.system, kind=row.kind, failed_at=row.failed_at)  # type: ignore[arg-type]
        )
    return by_item


def calendar_state(
    session: Session, item: ExtActionItem, *, reader_id: str | None
) -> CalendarState:
    """Whether the item is on its assignee's calendar, and the first thing
    missing if it is not -- the conditions ``calendar_sync._calendar_owner``
    applies, in its order, said out loud.

    ``not_connected`` is answered only when ``reader_id`` is the assignee:
    that somebody has not connected their calendar is a fact about their own
    account, and a teammate is told only that there is no event.
    """
    event = session.scalar(
        select(ExtCalendarEvent.event_id).where(
            ExtCalendarEvent.action_item_id == item.id, ExtCalendarEvent.event_id.is_not(None)
        )
    )
    if event:
        return CalendarState(state="sent")
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
    if reader_id == item.assignee_id and item.assignee_id not in users_with_integration(
        session, CALENDAR
    ):
        return CalendarState(state="none", reason="not_connected")
    return CalendarState(state="none")
