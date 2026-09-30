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
next edit. The ref records the **site** (cloud id) with the key: a key is
unique only within a site, so after a reconnect to another site the old key is
never written to -- the item gets a new issue there (#458 review).

**Deleting the item in Autune closes its issue, it does not delete it**
(``close_for_deleted_item``): the issue moves to a ``done`` status and gets a
comment saying Autune deleted the item. A wrongly extracted item should not
sit in the team's open work, and the team may have commented or worked on the
issue, which a delete would take with it (decided with the user, #458).

Status moves go through whatever transition the team's workflow offers into
that category; a workflow without one leaves the issue where it is, and so
does an issue already in a status of that category -- a person's "In Review"
stays. A status move that fails does not lose the issue: its key is kept.

The summary is one line of at most 255 characters, Jira's limit; a longer
description goes whole into the issue's description when it is made.

**The status comes back too** (``pull_status_changes``, every ten minutes): an
issue a person moved in Jira moves its item on the board, unless the board
moved since Autune last touched the issue -- then the board's edit goes out
and wins. Only the status; summary, due date and assignee stay Autune's.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, TeamMember, User, get_logger
from autune_integrations import IntegrationError, PermanentIntegrationError

from . import service
from .models import ExtActionItem, ExtExternalRef
from .schemas import ActionItemUpdate
from .service import _insert_if_absent_into

log = get_logger(__name__)

JIRA = "jira"
SUMMARY_LIMIT = 255

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
        keep_assignee: bool = False,
    ) -> bool: ...

    def move_to_category(self, issue_key: str, category: str) -> bool: ...

    def add_comment(self, issue_key: str, text: str) -> None: ...

    def status_category(self, issue_key: str) -> str | None: ...


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


def _summary(text: str) -> str:
    """One line, within Jira's limit -- a newline or a 256th character makes
    Jira answer 400."""
    line = " ".join(text.split())
    return line if len(line) <= SUMMARY_LIMIT else line[: SUMMARY_LIMIT - 1] + "…"


def sync_action_item_to_jira(
    session: Session,
    jira: JiraIssues,
    *,
    action_item_id: str,
    project_key: str,
    site: str,
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

    if ref.external_id and ref.site != site:
        # A key from another site (a reconnect since) names an issue that is
        # not ours here: never written, the item gets one on this site.
        log.info("extraction_jira_other_site", action_item_id=item.id)
    ours = bool(ref.external_id) and ref.site == site

    account = _assignee_account(session, jira, item)
    summary = _summary(item.description)
    updated = ours and jira.update_task(
        str(ref.external_id),
        summary,
        due_date=item.due_date,
        assignee_account_id=account,
        # Someone Autune could not find in Jira may have been assigned there by
        # hand; only an item with no assignee in Autune clears Jira's.
        keep_assignee=account is None and item.assignee_id is not None,
    )
    if not updated:
        # First send, an issue deleted in Jira since, or another site: make it.
        ref.external_id = jira.create_task(
            project_key,
            summary,
            description=item.description if summary != item.description else "",
            due_date=item.due_date,
            assignee_account_id=account,
        )
        ref.site = site
        ref.url = f"{site_url.rstrip('/')}/browse/{ref.external_id}" if site_url else None
        log.info("extraction_jira_created", action_item_id=item.id)
    else:
        log.info("extraction_jira_updated", action_item_id=item.id)

    category = CATEGORY.get(item.status)
    if category is not None:
        try:
            if jira.move_to_category(str(ref.external_id), category):
                # What the read-back compares against: Jira now shows what the
                # board shows (``pull_status_changes``).
                ref.synced_category = category
            else:
                log.info("extraction_jira_no_transition", action_item_id=item.id, category=category)
        except IntegrationError as exc:
            # The issue exists; losing its key here would make a second one on
            # the next edit (#458 review). The status waits for that edit.
            log.warning(
                "extraction_jira_move_failed",
                action_item_id=item.id,
                category=category,
                error=type(exc).__name__,
            )
    return ref


STATUS_OF = {category: status for status, category in CATEGORY.items()}
"""A Jira status category as the board's status -- ``CATEGORY`` read backwards."""

PULL_LIMIT = 100
"""Issues read back per team per run at most, the most recently made first. A
read is one request per issue; a team with more open work than this is read
over several runs."""


def pull_status_changes(
    session: Session, jira: JiraIssues, *, team_id: str, site: str, limit: int = PULL_LIMIT
) -> list[str]:
    """Read back the status people moved issues to in Jira (the mentoring of
    2026-09-28: follow each assignee's issues, not only create them). Returns
    the ids of items whose status changed, for the caller to sync onward.

    For each of the team's confirmed items with an issue on this site, the
    issue's status category is compared with ``synced_category``, what Autune
    last left it in or last read:

    - **Jira unchanged** -- nothing to read. A board edit that has not reached
      Jira yet is the outgoing sync's to send, and must not be undone here.
    - **Jira changed, the board did not** -- the item takes the matching status
      through the board's edit path (``service.update_action_item``), as a date
      moved on a person's calendar does (#435).
    - **Both changed** -- the board wins and its edit goes out; logged.

    A ref written before this existed has no ``synced_category`` and is taken
    to be in step with the board. The ref row is locked first, the order the
    outgoing sync takes, so the two never interleave on one item. An issue
    deleted in Jira (404) is left alone and logged: the next board edit makes
    it again, as it always has. Any other failure raises for the caller.
    """
    rows = session.execute(
        select(ExtExternalRef.action_item_id)
        .join(ExtActionItem, ExtActionItem.id == ExtExternalRef.action_item_id)
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .where(
            ExtExternalRef.system == JIRA,
            ExtExternalRef.site == site,
            ExtExternalRef.external_id.is_not(None),
            Meeting.team_id == team_id,
            ExtActionItem.status.in_(list(CATEGORY)),
        )
        .order_by(ExtExternalRef.created_at.desc())
        .limit(limit)
    ).all()

    moved: list[str] = []
    for (item_id,) in rows:
        ref = session.get(ExtExternalRef, (item_id, JIRA), with_for_update=True)
        item = session.get(ExtActionItem, item_id, populate_existing=True)
        if ref is None or item is None or not ref.external_id:
            continue
        board = CATEGORY.get(item.status)
        if board is None:
            continue
        try:
            in_jira = jira.status_category(str(ref.external_id))
        except PermanentIntegrationError as exc:
            if exc.details.get("upstream_status") != 404:
                raise
            log.info("extraction_jira_issue_gone", action_item_id=item_id)
            continue
        synced = ref.synced_category or board
        if in_jira is None or in_jira == synced or in_jira not in STATUS_OF:
            continue
        if board != synced:
            log.info("extraction_jira_both_moved", action_item_id=item_id)
            continue
        service.update_action_item(
            session, item, ActionItemUpdate(status=ActionStatus(STATUS_OF[in_jira]))
        )
        ref.synced_category = in_jira
        moved.append(item_id)
        log.info("extraction_jira_status_read_back", action_item_id=item_id, category=in_jira)
    session.flush()
    return moved


DELETED_NOTE = "Autune에서 삭제된 액션 아이템입니다. 이슈 기록은 남기고 닫았습니다."


def close_for_deleted_item(
    session: Session, jira: JiraIssues, *, action_item_id: str, site: str
) -> bool:
    """Before the board deletes an item: close its issue with a note. ``False``
    when the item never became an issue on this site -- a key from another site
    names someone else's issue. The ref row goes with the item."""
    ref = session.get(ExtExternalRef, (action_item_id, JIRA))
    if ref is None or not ref.external_id:
        return False
    if ref.site != site:
        log.info("extraction_jira_other_site", action_item_id=action_item_id)
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
