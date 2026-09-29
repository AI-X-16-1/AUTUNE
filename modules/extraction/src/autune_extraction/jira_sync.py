"""A confirmed action item as one Jira issue on the team's connected site (#82, #30).

Step 7's third destination, beside Notion and each person's calendar. When a
person confirms an item, the team's Jira project (one-click connected,
``autune_core.jira_access``) gets one issue; every later edit on the board
rewrites the fields Autune owns and moves the issue's status:

| Board | Jira |
| --- | --- |
| description | summary |
| due date | due date |
| assignee | assignee -- their Jira account found by email, or unassigned |
| to do / in progress / done | a status in category new / indeterminate / done |

**What leaves** is exactly that table. Not the transcript, not the source
utterances, not the meeting title. Looking the assignee up sends their email
address to the team's own Jira as a search; a site that hides emails, or a
person with no account there, leaves the issue unassigned rather than guessed.

**One issue per item**, claimed in ``ext_external_refs`` (``system = 'jira'``)
before it is created -- the Notion page's pattern -- so a confirmation
delivered twice makes one issue. An issue deleted in Jira is made again on the
next edit.

**Deleting the item in Autune closes its issue, it does not delete it**
(``close_for_deleted_item``): the issue moves to a ``done`` status and gets a
comment saying Autune deleted the item. A wrongly extracted item should not
sit in the team's open work, and the team may have commented or worked on the
issue, which a delete would take with it (decided with the user, #458).

Status moves go through whatever transition the team's workflow offers into
that category; a workflow without one leaves the issue where it is.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, TeamMember, User, get_logger
from autune_integrations import PermanentIntegrationError

from .models import ExtActionItem, ExtExternalRef
from .service import _insert_if_absent_into

log = get_logger(__name__)

JIRA = "jira"

CATEGORY = {
    ActionStatus.TODO.value: "new",
    ActionStatus.IN_PROGRESS.value: "indeterminate",
    ActionStatus.DONE.value: "done",
}


class JiraIssues(Protocol):
    """What this module calls -- ``JiraClient.for_cloud`` and ``FakeJira``."""

    def find_account_id(self, email: str) -> str | None: ...

    def create_task(
        self,
        project_key: str,
        summary: str,
        *,
        description: str = "",
        due_date: date | None = None,
        assignee_account_id: str | None = None,
        issue_type: str = "Task",
    ) -> str: ...

    def update_task(
        self,
        issue_key: str,
        summary: str,
        *,
        due_date: date | None,
        assignee_account_id: str | None,
    ) -> bool: ...

    def move_to_category(self, issue_key: str, category: str) -> bool: ...

    def add_comment(self, issue_key: str, text: str) -> None: ...


def _assignee_account(session: Session, jira: JiraIssues, item: ExtActionItem) -> str | None:
    """The assignee's Jira account, only for an identified member of the
    meeting's team (ADR 0007 -- a departed person is nobody's assignee)."""
    if not item.assignee_id:
        return None
    meeting = session.get(Meeting, item.meeting_id)
    member = (
        session.scalar(
            select(TeamMember.user_id).where(
                TeamMember.team_id == meeting.team_id, TeamMember.user_id == item.assignee_id
            )
        )
        if meeting is not None
        else None
    )
    user = session.get(User, item.assignee_id) if member is not None else None
    if user is None or not user.email:
        return None
    return jira.find_account_id(user.email)


def sync_action_item_to_jira(
    session: Session,
    jira: JiraIssues,
    *,
    action_item_id: str,
    project_key: str,
    site_url: str | None = None,
) -> ExtExternalRef | None:
    """Create the item's issue the first time, rewrite it every time after, and
    move it into the category of the item's status. ``None`` when there is
    nothing to send.

    The ref row is locked before the item is read (``populate_existing``), for
    the reason ``sync_action_item_to_notion`` gives: of two edits in flight, the
    one sending last must be the one that read last.
    """
    ref = session.get(ExtExternalRef, (action_item_id, JIRA), with_for_update=True)
    item = session.get(ExtActionItem, action_item_id, populate_existing=True)
    if item is None or item.status == ActionStatus.NEEDS_CONFIRMATION.value:
        return ref

    if ref is None:
        claimed = session.scalars(
            _insert_if_absent_into(session, ExtExternalRef)
            .values(action_item_id=item.id, system=JIRA, meeting_id=item.meeting_id)
            .on_conflict_do_nothing(index_elements=["action_item_id", "system"])
            .returning(ExtExternalRef.action_item_id)
        ).one_or_none()
        ref = session.get(
            ExtExternalRef, (item.id, JIRA), with_for_update=claimed is None, populate_existing=True
        )
        assert ref is not None

    account = _assignee_account(session, jira, item)
    updated = bool(ref.external_id) and jira.update_task(
        str(ref.external_id), item.description, due_date=item.due_date, assignee_account_id=account
    )
    if not updated:
        # First send, or an issue deleted in Jira since: make it.
        ref.external_id = jira.create_task(
            project_key, item.description, due_date=item.due_date, assignee_account_id=account
        )
        ref.url = f"{site_url.rstrip('/')}/browse/{ref.external_id}" if site_url else None
        log.info("extraction_jira_created", action_item_id=item.id)
    else:
        log.info("extraction_jira_updated", action_item_id=item.id)

    category = CATEGORY.get(item.status)
    if category is not None and not jira.move_to_category(str(ref.external_id), category):
        log.info("extraction_jira_no_transition", action_item_id=item.id, category=category)
    return ref


DELETED_NOTE = "Autune에서 삭제된 액션 아이템입니다. 이슈 기록은 남기고 닫았습니다."


def close_for_deleted_item(session: Session, jira: JiraIssues, *, action_item_id: str) -> bool:
    """Before the board deletes an item: close its issue with a note. ``False``
    when the item never became an issue. The ref row goes with the item."""
    ref = session.get(ExtExternalRef, (action_item_id, JIRA))
    if ref is None or not ref.external_id:
        return False
    key = str(ref.external_id)
    try:
        if not jira.move_to_category(key, "done"):
            log.info(
                "extraction_jira_no_transition", action_item_id=action_item_id, category="done"
            )
        jira.add_comment(key, DELETED_NOTE)
    except PermanentIntegrationError as exc:
        # Already gone -- deleted in Jira, or with its project. Nothing to close.
        if exc.details.get("upstream_status") != 404:
            raise
        log.info("extraction_jira_already_gone", action_item_id=action_item_id)
        return False
    log.info("extraction_jira_closed_with_item", action_item_id=action_item_id)
    return True
