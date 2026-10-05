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

**The status comes back too** (``read_back``, every ten minutes): an issue a
person moved in Jira moves its item on the board, unless the board moved since
Autune last touched the issue -- then the board keeps its status, and its own
edit's outgoing sync is what brings Jira along. Only the status; summary, due
date and assignee stay Autune's.

**A confirmed decision is an issue too** (``sync_decision_to_jira``, the user,
2026-10-04), beside its Notion page: summary "[결정] " and the statement in the
wording that stands, filed straight into a ``done`` status -- a decision is
settled, not work to pick up. Rewording it rewrites the issue. Taking the
confirmation back, deleting the decision or a rerun dropping it retires the
issue the way a decision's Notion page is retired (#669): retitled to
``DECISION_PUT_BACK_TEXT``, its description cleared, a note added, closed --
never deleted, since the team may have commented on it. Nothing goes before a
person confirms (#246), and nothing but the statement: no meeting title, no
source utterances, no names.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, TeamMember, User, get_logger
from autune_integrations import IntegrationError, PermanentIntegrationError

from . import service
from .models import ExtActionItem, ExtDecision, ExtDecisionRef, ExtDecisionReview, ExtExternalRef
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
        description: str | None = None,
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

    **An item moved back to 확인 필요 keeps its issue, and the issue keeps
    following its text** (#657): summary, description, due date and assignee
    are rewritten, the status is left where the team has it (Jira has no
    category for a draft), and an issue deleted in Jira is not made again --
    its ref row goes, so the item stops counting as one with a copy outside
    (#672).
    Without this a line deleted or corrected in Autune stayed in the issue of
    an item that happened to be a draft again. An item never confirmed has no
    issue and gets none.
    """
    ref = session.get(ExtExternalRef, (action_item_id, JIRA), with_for_update=True)
    item = session.get(ExtActionItem, action_item_id, populate_existing=True)
    if item is None:
        return ref
    moved_back = item.status == ActionStatus.NEEDS_CONFIRMATION.value
    if moved_back and not (ref is not None and ref.external_id and ref.site == site):
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
    # The full text when the summary could not hold it, else nothing -- on an
    # update too, so a description that changed (or was a deleted person's
    # words, #587) does not stay behind in Jira (#601 review).
    description = item.description if summary != item.description else ""
    updated = ours and jira.update_task(
        str(ref.external_id),
        summary,
        due_date=item.due_date,
        assignee_account_id=account,
        # Someone Autune could not find in Jira may have been assigned there by
        # hand; only an item with no assignee in Autune clears Jira's.
        keep_assignee=account is None and item.assignee_id is not None,
        description=description,
    )
    if moved_back:
        log.info("extraction_jira_updated_moved_back", action_item_id=item.id, found=bool(updated))
        if not updated:
            session.delete(ref)
            session.flush()
            return None
        return ref
    if not updated:
        # First send, an issue deleted in Jira since, or another site: make it.
        ref.external_id = jira.create_task(
            project_key,
            summary,
            description=description,
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
                # board shows (``read_back``).
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
"""Issues read back per team per run at most, the least recently read first
(``ExtExternalRef.pulled_at``). A read is one request per issue; a team with
more issues than this -- done ones count too, since a done issue can be
reopened in Jira -- is read over several runs."""

PER_TEAM = frozenset({401})
"""Refusals that say the grant itself is gone, not this one issue: every other
read of the run would fail the same way, so the run stops (``read_back``)."""


def pull_candidates(
    session: Session, *, team_id: str, site: str, limit: int = PULL_LIMIT
) -> list[tuple[str, str]]:
    """The team's confirmed items with an issue on this site, as ``(item id,
    issue key)``, least recently read first -- the ones ``read_back`` reads
    this run. No row is locked: each read takes its own locks."""
    rows = session.execute(
        select(ExtExternalRef.action_item_id, ExtExternalRef.external_id)
        .join(ExtActionItem, ExtActionItem.id == ExtExternalRef.action_item_id)
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .where(
            ExtExternalRef.system == JIRA,
            ExtExternalRef.site == site,
            ExtExternalRef.external_id.is_not(None),
            Meeting.team_id == team_id,
            ExtActionItem.status.in_(list(CATEGORY)),
        )
        .order_by(ExtExternalRef.pulled_at.asc().nulls_first(), ExtExternalRef.created_at.desc())
        .limit(limit)
    ).all()
    return [(item_id, str(key)) for item_id, key in rows]


def read_back(session: Session, jira: JiraIssues, *, item_id: str, key: str, site: str) -> bool:
    """Read back the status a person moved one issue to in Jira (the mentoring
    of 2026-09-28: follow each assignee's issues, not only create them).
    ``True`` when the item's status changed, for the caller to sync onward.
    One call is one item, meant to be its own transaction
    (``tasks.pull_jira_changes``), so a failure costs this item only.

    The issue's status category is compared with ``synced_category``, what
    Autune last left it in or last read:

    - **Jira unchanged** -- nothing to read. A board edit that has not reached
      Jira yet is the outgoing sync's to send, and must not be undone here.
    - **Jira changed, the board did not** -- the item takes the matching status
      through the board's edit path (``service.update_action_item``), as a date
      moved on a person's calendar does (#435).
    - **Both changed** -- the board keeps its status, and Jira's is recorded as
      the new baseline, so the same move is logged once, not every run.
      Nothing is sent from here: the board's edit reaches Jira through the
      outgoing sync that edit queued, or the next edit's. Sending the board's
      status here would also undo a person's move whenever the board only
      *looks* moved -- a baseline taken while the two already disagreed.

    A ref with no ``synced_category`` has no baseline to compare with: it was
    written before the read-back existed, or its issue never took the board's
    status (no transition in the workflow, or the move failed). Its issue's
    category is recorded as the baseline and the board is left alone -- the
    board may hold an edit Jira never got, and only a move made after the
    baseline is a person's. (#548 review.)

    Jira is read first, with no row locked, so a slow request holds up no
    board edit. Then both rows are locked, the item first: deleting an item
    locks it and then its ref (``ON DELETE CASCADE``), so taking them in that
    order never deadlocks with a deletion, and a board edit cannot land
    between this read and the write below. The outgoing sync locks only the
    ref, so waiting on it here reads the ``synced_category`` it left. If that
    changed while Jira was being read, the read may predate the outgoing move
    and would undo the board's edit, so it is dropped; so is one whose key
    changed meanwhile (the issue made again). The next run reads both afresh.

    An issue Jira refuses to show -- deleted (404), or hidden from the grant
    (403) -- is logged by status and skipped; the next board edit makes a
    deleted one again, as it always has. A refusal of the grant itself
    (``PER_TEAM``) and any transient failure raise for the caller.
    """
    seen = session.scalar(
        select(ExtExternalRef.synced_category).where(
            ExtExternalRef.action_item_id == item_id, ExtExternalRef.system == JIRA
        )
    )
    try:
        in_jira = jira.status_category(key)
    except PermanentIntegrationError as exc:
        status = exc.details.get("upstream_status")
        if status in PER_TEAM:
            raise
        log.info("extraction_jira_read_refused", action_item_id=item_id, upstream_status=status)
        in_jira = None

    item = session.get(ExtActionItem, item_id, with_for_update=True, populate_existing=True)
    ref = session.get(ExtExternalRef, (item_id, JIRA), with_for_update=True, populate_existing=True)
    if ref is None or item is None or ref.external_id != key or ref.site != site:
        return False
    ref.pulled_at = datetime.now(UTC)
    synced = ref.synced_category
    if synced != seen:
        # The outgoing sync moved the issue while Jira was being read: what was
        # read may be from before its move. The next run reads it afresh.
        log.info("extraction_jira_read_overtaken", action_item_id=item_id)
        return False
    board = CATEGORY.get(item.status)
    if board is None or in_jira is None or in_jira not in STATUS_OF:
        return False
    ref.synced_category = in_jira
    if synced is None:
        log.info("extraction_jira_baseline_recorded", action_item_id=item_id, category=in_jira)
        return False
    if in_jira == synced:
        return False
    if board != synced:
        log.info("extraction_jira_both_moved", action_item_id=item_id, category=in_jira)
        return False
    service.update_action_item(
        session, item, ActionItemUpdate(status=ActionStatus(STATUS_OF[in_jira]))
    )
    log.info("extraction_jira_status_read_back", action_item_id=item_id, category=in_jira)
    return True


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
    if not close_issue(jira, str(ref.external_id)):
        log.info("extraction_jira_already_gone", action_item_id=action_item_id)
        return False
    log.info("extraction_jira_closed_with_item", action_item_id=action_item_id)
    return True


def close_issue(jira: JiraIssues, key: str) -> bool:
    """Move an issue to done and leave ``DELETED_NOTE`` on it. ``False`` when it
    is already gone -- deleted in Jira, or with its project. Shared by the
    deletion itself and ``tasks.drain_external_cleanup``'s retry (#692)."""
    try:
        if not jira.move_to_category(key, "done"):
            log.info("extraction_jira_no_transition", category="done")
        jira.add_comment(key, DELETED_NOTE)
    except PermanentIntegrationError as exc:
        if exc.details.get("upstream_status") != 404:
            raise
        return False
    return True


# --- decisions (2026-10-04) ------------------------------------------------------

DECISION_PREFIX = "[결정] "
"""What marks a decision's issue among the team's tasks."""

DECISION_RETIRED_NOTE = "Autune에서 확정이 취소된 결정입니다. 이슈 기록은 남기고 닫았습니다."


def _confirmed(
    decision: ExtDecision | None, review: ExtDecisionReview | None
) -> tuple[ExtDecision, ExtDecisionReview] | None:
    if decision is None or review is None or review.status != "confirmed":
        return None
    return decision, review


def sync_decision_to_jira(
    session: Session,
    jira: JiraIssues,
    *,
    decision_id: str,
    project_key: str,
    site: str,
    site_url: str | None = None,
) -> ExtDecisionRef | None:
    """A confirmed decision as one issue: made the first time, rewritten after,
    filed into a ``done`` status. A decision no longer confirmed -- put back,
    deleted, dropped by a rerun -- has its issue retired. ``None`` when there
    is nothing to send.

    The claim, the lock-then-reread order and the site rule are
    ``sync_action_item_to_jira``'s: a confirmation delivered twice makes one
    issue, of two rewordings in flight the one that read last sends last, and
    a key from another site is never written to.
    """
    ref = session.get(ExtDecisionRef, (decision_id, JIRA), with_for_update=True)
    decision = session.get(ExtDecision, decision_id, populate_existing=True)
    review = session.get(ExtDecisionReview, decision_id, populate_existing=True)
    settled = _confirmed(decision, review)
    if settled is None:
        if ref is not None and ref.external_id:
            _retire_decision_issue(jira, ref, site)
        return ref
    decision, review = settled

    if ref is None:
        claimed = session.scalars(
            _insert_if_absent_into(session, ExtDecisionRef)
            .values(decision_id=decision.id, system=JIRA, meeting_id=decision.meeting_id)
            .on_conflict_do_nothing(index_elements=["decision_id", "system"])
            .returning(ExtDecisionRef.decision_id)
        ).one_or_none()
        ref = session.get(
            ExtDecisionRef,
            (decision.id, JIRA),
            with_for_update=claimed is None,
            populate_existing=True,
        )
        assert ref is not None
        if claimed is None:
            # Another sync made the row first; read the verdict again under
            # its lock, as the action item's claim race does.
            settled = _confirmed(
                session.get(ExtDecision, decision_id, populate_existing=True),
                session.get(ExtDecisionReview, decision_id, populate_existing=True),
            )
            if settled is None:
                if ref.external_id:
                    _retire_decision_issue(jira, ref, site)
                return ref
            decision, review = settled

    statement = service._confirmed_statement(decision, review)
    summary = _summary(DECISION_PREFIX + statement)
    # The whole statement when the summary could not hold it, else nothing --
    # on an update too, so a reworded or deleted line does not stay behind.
    description = statement if summary != DECISION_PREFIX + " ".join(statement.split()) else ""
    if ref.external_id and ref.site != site:
        log.info("extraction_jira_decision_other_site", decision_id=decision.id)
    ours = bool(ref.external_id) and ref.site == site
    updated = ours and jira.update_task(
        str(ref.external_id),
        summary,
        due_date=None,
        assignee_account_id=None,
        keep_assignee=True,
        description=description,
    )
    if not updated:
        ref.external_id = jira.create_task(project_key, summary, description=description)
        ref.site = site
        ref.url = f"{site_url.rstrip('/')}/browse/{ref.external_id}" if site_url else None
        log.info("extraction_jira_decision_created", decision_id=decision.id)
    else:
        log.info("extraction_jira_decision_updated", decision_id=decision.id)
    try:
        if not jira.move_to_category(str(ref.external_id), "done"):
            log.info("extraction_jira_no_transition", decision_id=decision.id, category="done")
    except IntegrationError as exc:
        # The issue exists and its key is kept; the status waits for the next
        # sync rather than a second issue being made.
        log.warning(
            "extraction_jira_move_failed", decision_id=decision.id, error=type(exc).__name__
        )
    return ref


def _retire_decision_issue(jira: JiraIssues, ref: ExtDecisionRef, site: str) -> None:
    """Take a decision's issue out of what reads as settled: retitled to
    ``DECISION_PUT_BACK_TEXT`` with its description cleared, so the statement
    is not what the issue keeps, then a note and a ``done`` status. The row
    forgets the issue: confirming again makes a new one. An issue on another
    site, or deleted in Jira, cannot be reached and is only forgotten."""
    key = str(ref.external_id)
    if ref.site == site:
        try:
            if jira.update_task(
                key,
                service.DECISION_PUT_BACK_TEXT,
                due_date=None,
                assignee_account_id=None,
                keep_assignee=True,
                description="",
            ):
                jira.add_comment(key, DECISION_RETIRED_NOTE)
                if not jira.move_to_category(key, "done"):
                    log.info(
                        "extraction_jira_no_transition",
                        decision_id=ref.decision_id,
                        category="done",
                    )
        except PermanentIntegrationError as exc:
            if exc.details.get("upstream_status") != 404:
                raise
    else:
        log.info("extraction_jira_decision_other_site", decision_id=ref.decision_id)
    ref.external_id = None
    ref.url = None
    log.info("extraction_jira_decision_retired", decision_id=ref.decision_id)


def decision_issues_to_retire(session: Session) -> list[tuple[str, str]]:
    """(decision id, meeting id) of every decision issue whose decision is gone
    or no longer confirmed -- for the sweep that retires what a failed call
    left behind (``tasks.retire_decision_issues``). Ids only."""
    confirmed = (
        select(ExtDecisionReview.decision_id)
        .where(
            ExtDecisionReview.decision_id == ExtDecisionRef.decision_id,
            ExtDecisionReview.status == "confirmed",
        )
        .exists()
    )
    return [
        (decision_id, meeting_id)
        for decision_id, meeting_id in session.execute(
            select(ExtDecisionRef.decision_id, ExtDecisionRef.meeting_id).where(
                ExtDecisionRef.system == JIRA,
                ExtDecisionRef.external_id.is_not(None),
                ~confirmed,
            )
        ).tuples()
    ]
