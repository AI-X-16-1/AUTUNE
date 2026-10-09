"""HTTP entry point for module B.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/extraction`` is applied by apps/api; declare paths relative to it.

**Every route but ``/health`` takes ``CurrentUser`` and, before anything else,
resolves what it names through ``service.require_readable_meeting`` /
``readable_action_item`` / ``readable_decision``** (#189). A caller outside the
meeting's team gets the same 404 as an unknown id; the list is narrowed to the
caller's teams instead. ``tests/unit/test_route_auth.py`` fails for a route that
does not take the user. The ``/dev`` page is outside this rule: it is served
only with ``AUTUNE_ENV=local`` and its own opt-in (see ``dev_routes_enabled``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_contracts.extraction import ExtractionResult
from autune_core import CurrentUser, Meeting, SessionDep, User
from autune_core.errors import ConflictError, NotFoundError
from autune_core.settings import get_settings as get_core_settings

from . import (
    attempts,
    jira_issues,
    leave_calendar,
    materials,
    notion_connect,
    projects,
    service,
    sync_log,
    sync_state,
    tasks,
)
from .config import get_settings
from .notion_setup import NotionSetupError
from .schemas import (
    ActionItemCreate,
    ActionItemDetail,
    ActionItemRead,
    ActionItemUpdate,
    Assignable,
    BulkActionItems,
    BulkActionResult,
    CarriedOver,
    ConfirmationAnswerIn,
    DecisionCreate,
    DecisionDetail,
    DecisionReviewUpdate,
    DueReminderSetting,
    DueReminderSettingIn,
    ExtractionState,
    JiraProjectIssues,
    MaterialRead,
    MaterialWrite,
    MeetingNoteUpdate,
    MeetingReview,
    MeetingSummary,
    MyConfirmation,
    NameSuggestion,
    NotificationPause,
    NotificationPauseRead,
    Outbound,
    ProjectPlacement,
    ProjectRead,
    ProjectSendReport,
    ProjectSendRequest,
    ProjectSendResult,
    ProjectWrite,
    ReviewDecision,
    SyncLogRead,
    TeamRead,
)

router = APIRouter()


def dev_routes_enabled() -> bool:
    """The local-only page for connecting Notion by hand, until S28 exists.

    It has no auth, so it needs both ``AUTUNE_ENV=local`` and an explicit
    ``AUTUNE_EXTRACTION_DEV_ROUTES=true``: ``local`` is what every checkout and
    the demo stack run under, and being one must not be enough to serve this.
    (A deployment that forgets ``AUTUNE_ENV`` is ``production`` since #446.) See
    ``dev/routes.py`` and #401 for why a module may write team_integrations
    here at all."""
    return get_core_settings().env == "local" and get_settings().dev_routes


if dev_routes_enabled():
    from .dev import router as dev_router

    router.include_router(dev_router, prefix="/dev")


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "extraction", "status": "ok"}


@router.get("/results/{meeting_id}", response_model=ExtractionResult)
def get_results(meeting_id: str, session: SessionDep, reader: CurrentUser) -> ExtractionResult:
    """Everything this meeting produced, including every correction since.

    A meeting with nothing extracted yet answers with empty lists, not a 404:
    the meeting exists and has, so far, produced nothing. Only a meeting that
    does not exist -- or is not the caller's team's -- is not found.
    """
    service.require_readable_meeting(session, meeting_id, reader)
    return service.result_for_meeting(session, meeting_id)


@router.get("/meetings/{meeting_id}/extraction", response_model=ExtractionState)
def get_extraction_state(
    meeting_id: str, session: SessionDep, reader: CurrentUser
) -> ExtractionState:
    """Whether this meeting's extraction went through, failed, or is waiting to
    run again -- what the 액션 tab says above its board. Members of the
    meeting's team only; anyone else gets the 404 an unknown meeting gets."""
    service.require_readable_meeting(session, meeting_id, reader)
    return attempts.state(session, meeting_id)


@router.post(
    "/meetings/{meeting_id}/extraction",
    response_model=ExtractionState,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_extraction(
    meeting_id: str, session: SessionDep, reader: CurrentUser
) -> ExtractionState:
    """Extract this meeting's action items and decisions again -- the 액션
    tab's "다시 추출" (the user, 2026-10-06).

    Accepted, not done: the request is recorded and the worker runs it within
    a minute (``tasks.run_requested_extractions``). It is the same run as the
    automatic one, so an item list a person has edited is kept and only the
    model's own rows are replaced. Any member of the meeting's team may ask. A
    meeting with no transcript yet has nothing to extract (409), and a second
    request within ``attempts.REQUEST_COOLDOWN`` is refused (429) rather than
    started beside the first."""
    service.require_readable_meeting(session, meeting_id, reader)
    if not attempts.transcribed(session, meeting_id):
        raise ConflictError("this meeting has no transcript to extract from yet")
    if not attempts.claim_request(session, meeting_id):
        raise sync_state.RetryTooSoonError(
            "this meeting's extraction was asked for a moment ago",
            retry_after_seconds=int(attempts.REQUEST_COOLDOWN.total_seconds()),
        )
    session.commit()
    return attempts.state(session, meeting_id)


@router.get("/action-items", response_model=list[ActionItemRead])
def list_action_items(
    session: SessionDep,
    reader: CurrentUser,
    meeting_id: str | None = None,
    assignee_id: str | None = None,
    # Aliased so the parameter does not shadow ``fastapi.status`` in this module.
    status_filter: Annotated[ActionStatus | None, Query(alias="status")] = None,
    due_before: date | None = None,
) -> list[ActionItemRead]:
    """Items for the board, by any combination of the four filters, from the
    caller's teams' meetings only."""
    return service.list_action_items(
        session,
        meeting_id=meeting_id,
        assignee_id=assignee_id,
        status=status_filter,
        due_before=due_before,
        visible_to=reader.id,
    )


@router.get("/carried-over/{meeting_id}", response_model=CarriedOver)
def get_carried_over(meeting_id: str, session: SessionDep, reader: CurrentUser) -> CarriedOver:
    """What the team's earlier meetings left open, for the popup this
    meeting's review opens with (WBS 4.8). Members of the meeting's team only."""
    service.require_readable_meeting(session, meeting_id, reader)
    return service.carried_over(session, meeting_id)


@router.get("/meetings/{meeting_id}/assignable", response_model=list[Assignable])
def get_assignable(meeting_id: str, session: SessionDep, reader: CurrentUser) -> list[Assignable]:
    """Who an item of this meeting can be assigned to: the meeting's team, by
    name, for the assignee picker. Members of that team only -- the same
    404 as an unknown meeting for anyone else."""
    service.require_readable_meeting(session, meeting_id, reader)
    return service.assignable_members(session, meeting_id)


@router.get("/action-items/{action_item_id}", response_model=ActionItemDetail)
def get_action_item(
    action_item_id: str, session: SessionDep, reader: CurrentUser
) -> ActionItemDetail:
    """One item and the text of the utterances it came from, for the drawer."""
    item = service.readable_action_item(session, action_item_id, reader)
    return service.read_detail(session, item, reader_id=reader.id)


@router.post("/action-items/{action_item_id}/sync", status_code=status.HTTP_202_ACCEPTED)
def retry_action_item_sync(
    action_item_id: str,
    session: SessionDep,
    reader: CurrentUser,
    background: BackgroundTasks,
) -> dict[str, bool]:
    """Send the item to the team's connected tools again -- the "다시 시도" beside
    a failed copy (#680). The same call an edit queues, after the response;
    what it does is decided there (create, update, or nothing to send).
    Members of the meeting's team only. An item that was never confirmed has
    nothing outside to retry and queues nothing."""
    item = service.readable_action_item(session, action_item_id, reader)
    queued = service.copies_follow(session, item)
    if queued:
        # A second press within ``RETRY_COOLDOWN`` is refused (429) rather than
        # running the three syncs again (lsh2217, review of #754).
        if not sync_state.claim_retry(session, item.id):
            raise sync_state.RetryTooSoonError(
                "this item was sent again a moment ago",
                retry_after_seconds=int(sync_state.RETRY_COOLDOWN.total_seconds()),
            )
        session.commit()
        background.add_task(tasks.sync_after_confirmation, item.id)
    return {"queued": queued}


@router.post("/action-items", response_model=ActionItemRead, status_code=status.HTTP_201_CREATED)
def create_action_item(
    payload: ActionItemCreate,
    session: SessionDep,
    reader: CurrentUser,
    background: BackgroundTasks,
) -> ActionItemRead:
    """Add an item the model missed.

    ADR 0006 ranks recall above precision because a wrong item costs a click and
    a missing one costs re-reading the meeting. This is the route that makes the
    second recoverable.

    The item is confirmed as written (``service.create_action_item``), so its
    Notion page, Jira issue and due-date event are queued here after the
    commit, exactly as ``update_action_item`` queues them on confirmation.
    """
    service.require_readable_meeting(session, payload.meeting_id, reader)
    item = service.create_action_item(session, payload)
    # The response is built before the commit. ``read_model`` reads the
    # candidate threshold, and a threshold that does not parse (a 7 in .env)
    # used to fail here after the item was already saved: the client got a 500
    # for a write that had happened, and a retry made a second item. Failing
    # first lets ``get_session`` roll it back.
    response = service.read_one(session, item, reader_id=reader.id)
    session.commit()
    if service.copies_follow(session, item):
        background.add_task(tasks.sync_after_confirmation, item.id)
    return response


@router.patch("/action-items/{action_item_id}", response_model=ActionItemRead)
def update_action_item(
    action_item_id: str,
    payload: ActionItemUpdate,
    session: SessionDep,
    reader: CurrentUser,
    background: BackgroundTasks,
) -> ActionItemRead:
    """Edit or close an item. Confirming it queues its Notion page (#30); an
    edit to an item that has a page queues an update to it -- moving it back to
    확인 필요 included."""
    item = service.readable_action_item(session, action_item_id, reader)
    item = service.update_action_item(session, item, payload)
    # Before the commit, for the reason ``create_action_item`` gives: an edit
    # answered with a 500 must not also have been saved, or it counts twice
    # in edit cost when the client retries.
    response = service.read_one(session, item, reader_id=reader.id)
    session.commit()
    # After the response, so the sync reads the committed row and the board is
    # not held on Notion. Confirming or any later edit queues the same task --
    # ``sync_action_item_to_notion`` decides create vs. update from whether the
    # claim already exists. An unconfirmed item queues it only when it has a
    # copy outside: one moved back to 확인 필요 updates that page's status
    # (decided with the user, 2026-10-01); one never confirmed has nothing to
    # send. The same rule a deleted speech and a corrected line use (#657).
    if service.copies_follow(session, item):
        background.add_task(tasks.sync_after_confirmation, item.id)
    else:
        # No copy of its own to follow, but the project minutes of its meeting
        # may carry it: an item moved back to 확인 필요, or edited, whose only
        # copy outside is those minutes (#787 review). ``sync_after_confirmation``
        # ends with the same refresh.
        background.add_task(tasks.refresh_project_minutes, item.meeting_id)
    return response


@router.post("/action-items/bulk", response_model=BulkActionResult)
def bulk_action_items(
    payload: BulkActionItems,
    session: SessionDep,
    reader: CurrentUser,
    background: BackgroundTasks,
) -> BulkActionResult:
    """Confirm or delete several items in one go (the user, 2026-10-04).

    Only items still in 확인 필요 are touched: this is the triage of what the
    model drafted, not a way to move or delete the board. Each item goes
    through the path a single one takes -- ``service.update_action_item``
    records a confirmation as an edit and queues the item's calendar event,
    Notion page and Jira issue after the commit; a deletion closes its outside
    copies first, as ``delete_action_item`` does. An id the reader cannot read,
    or one past 확인 필요, is reported as skipped, the same for both: telling
    them apart would say which ids exist on other teams.
    """
    confirmed: list[str] = []
    deleted: list[str] = []
    skipped: list[str] = []
    for action_item_id in dict.fromkeys(payload.ids):
        try:
            item = service.readable_action_item(session, action_item_id, reader)
        except NotFoundError:
            skipped.append(action_item_id)
            continue
        if item.status != ActionStatus.NEEDS_CONFIRMATION.value:
            skipped.append(action_item_id)
            continue
        if payload.action == "confirm":
            service.update_action_item(session, item, ActionItemUpdate(status=ActionStatus.TODO))
            confirmed.append(item.id)
        else:
            tasks.remove_calendar_event(item.id)
            tasks.close_jira_issue(item.id)
            tasks.trash_notion_page(item.id)
            service.delete_action_item(session, item)
            deleted.append(action_item_id)
    session.commit()
    for action_item_id in confirmed:
        background.add_task(tasks.sync_after_confirmation, action_item_id)
    return BulkActionResult(confirmed=confirmed, deleted=deleted, skipped=skipped)


@router.delete("/action-items/{action_item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_action_item(
    action_item_id: str, session: SessionDep, reader: CurrentUser, background: BackgroundTasks
) -> None:
    """Delete an item the model got wrong.

    Real deletion. ``privacy.md`` allows no soft deletes and no tombstones
    holding content; the edit-cost counter records that it happened without
    keeping what was deleted.

    Its due-date event comes off its assignee's calendar first
    (``tasks.remove_calendar_event``, #435), its Jira issue is closed with a
    note (``tasks.close_jira_issue``, #82) and its Notion page goes to Notion's
    trash (``tasks.trash_notion_page``, #467): once the rows cascade away none
    of them can be found again. The project minutes it went out in are
    rewritten after (``tasks.refresh_project_minutes``).
    """
    item = service.readable_action_item(session, action_item_id, reader)
    tasks.remove_calendar_event(item.id)
    tasks.close_jira_issue(item.id)
    tasks.trash_notion_page(item.id)
    meeting_id = item.meeting_id
    service.delete_action_item(session, item)
    session.commit()
    # The project minutes it was in, when they went out, lose it too.
    background.add_task(tasks.refresh_project_minutes, meeting_id)


@router.post("/action-items/{action_item_id}/close", response_model=ActionItemRead)
def close_action_item(
    action_item_id: str, session: SessionDep, reader: CurrentUser, background: BackgroundTasks
) -> ActionItemRead:
    """Close a confirmed item that will not be finished -- dropped, overtaken,
    no longer needed (#856). The board's way to what ``tools.close_action_item``
    does after an approval, with the same refusals: an item still waiting for
    confirmation has nothing a person agreed to close, a finished one is
    finished, and one already closed is not closed twice.

    Not a ``PATCH`` of the status: the item ends ``done`` either way, and what
    tells a close from finished work is the event kept
    (``service.close_without_finishing``), which a status edit does not write.
    Moving the status back re-opens it, as it does a finished item.
    """
    item = service.readable_action_item(session, action_item_id, reader)
    # Held to the commit, as the tool holds it: a second close, or an edit of
    # the status, waits and then reads what this one left (review of #979).
    session.refresh(item, with_for_update=True)
    if item.status == ActionStatus.NEEDS_CONFIRMATION.value:
        raise ConflictError("an item waiting for confirmation cannot be closed")
    if item.status == ActionStatus.DONE.value and service.closed_unfinished(session, [item.id]):
        raise ConflictError("this item is already closed")
    if not service.close_without_finishing(session, item):
        raise ConflictError("this item is already done")
    response = service.read_one(session, item, reader_id=reader.id)
    session.commit()
    # Its copies outside follow as they follow any change of status.
    background.add_task(tasks.sync_after_confirmation, item.id)
    return response


@router.get("/reviews/{meeting_id}", response_model=MeetingReview)
def get_review(meeting_id: str, session: SessionDep, reader: CurrentUser) -> MeetingReview:
    """What needs a person in this meeting before anything is sent (S15, #246)."""
    service.require_readable_meeting(session, meeting_id, reader)
    return service.review_for_meeting(session, meeting_id)


@router.get("/decisions/{decision_id}", response_model=DecisionDetail)
def get_decision(decision_id: str, session: SessionDep, reader: CurrentUser) -> DecisionDetail:
    """One decision and the text of the utterances it was settled in (S15)."""
    decision = service.readable_decision(session, decision_id, reader)
    return service.read_decision_detail(session, decision)


@router.patch("/decisions/{decision_id}", response_model=ReviewDecision)
def review_decision(
    decision_id: str,
    payload: DecisionReviewUpdate,
    session: SessionDep,
    reader: CurrentUser,
    background: BackgroundTasks,
) -> ReviewDecision:
    """Confirm, reject or reword a proposed decision, or put it back to pending.

    Confirming it sends its Notion page (#30); rewording an already-confirmed
    decision updates the same page instead of leaving it stale; taking the
    confirmation back takes the page out of Notion (#669), and the decision
    out of the project minutes that carried it."""
    decision = service.readable_decision(session, decision_id, reader)
    meeting_id = decision.meeting_id
    # Built before the commit, for the reason ``create_action_item`` gives.
    response = service.review_decision(session, decision, payload)
    session.commit()
    # Confirming or any later reword both queue the same task --
    # ``sync_decision_to_notion`` decides create vs. update from whether the
    # claim already exists. A decision put back to pending or rejected queues
    # it too while it has a page: the same task retires that page (#669).
    if response.status == "confirmed" or service.decision_has_page(session, decision_id):
        background.add_task(tasks.sync_decision_after_confirmation, decision_id)
    else:
        # No page to retire, but the meeting's project minutes may have gone to
        # Slack or Jira alone and still state it (#787 review). The task above
        # ends with the same refresh.
        background.add_task(tasks.refresh_project_minutes, meeting_id)
    return response


@router.get("/summary/{meeting_id}", response_model=MeetingSummary)
def get_summary(meeting_id: str, session: SessionDep, reader: CurrentUser) -> MeetingSummary:
    """S15's 요약 tab (#421): B's rows in three levels, and the team's memo."""
    service.require_readable_meeting(session, meeting_id, reader)
    return service.meeting_summary(session, meeting_id)


@router.put("/summary/{meeting_id}/note", response_model=MeetingSummary)
def put_summary_note(
    meeting_id: str, payload: MeetingNoteUpdate, session: SessionDep, reader: CurrentUser
) -> MeetingSummary:
    """Replace the team's memo; blank removes it. Any member, like every other
    correction on the review screen."""
    service.require_readable_meeting(session, meeting_id, reader)
    service.set_meeting_note(session, meeting_id, payload.body)
    response = service.meeting_summary(session, meeting_id)
    session.commit()
    return response


@router.post("/summary/{meeting_id}/projects/assign", response_model=MeetingSummary)
def assign_summary_projects(
    meeting_id: str, session: SessionDep, reader: CurrentUser
) -> MeetingSummary:
    """Place the meeting's decisions and items in the team's projects again, by
    the rules (``projects.assign_meeting``) -- after a project was added or
    renamed. What a person placed stays."""
    service.require_readable_meeting(session, meeting_id, reader)
    projects.assign_meeting(session, meeting_id)
    response = service.meeting_summary(session, meeting_id)
    session.commit()
    return response


@router.get("/projects/suggestions", response_model=list[NameSuggestion])
def project_name_suggestions(
    team_id: str, session: SessionDep, reader: CurrentUser
) -> list[NameSuggestion]:
    """Words that came up in several of the team's latest meetings that no
    project is named or aliased by, members' names left out
    (``projects.suggest_names``) -- words and counts only."""
    team = _member_team(session, reader, None, team_id)
    return [
        NameSuggestion(word=word, count=count)
        for word, count in projects.suggest_names(session, team)
    ]


@router.post("/summary/{meeting_id}/projects/send", response_model=ProjectSendReport)
def send_summary_projects(
    meeting_id: str, payload: ProjectSendRequest, session: SessionDep, reader: CurrentUser
) -> ProjectSendReport:
    """Send each project's confirmed decisions and items, as "팀-프로젝트-날짜",
    to the chosen tools (``project_send``). Sending again updates the same
    copies. Any member, like confirming."""
    service.require_readable_meeting(session, meeting_id, reader)
    sent, unsorted = tasks.send_project_minutes(
        session, meeting_id, payload.targets, sender_id=reader.id
    )
    session.commit()
    return ProjectSendReport(
        results=[
            ProjectSendResult(
                project_id=s.project_id,
                project_name=s.project_name,
                target=s.target,  # type: ignore[arg-type]
                outcome=s.outcome,  # type: ignore[arg-type]
            )
            for s in sent
        ],
        unsorted=unsorted,
    )


@router.get("/projects/mine", response_model=list[ProjectRead])
def my_projects(session: SessionDep, reader: CurrentUser) -> list[ProjectRead]:
    """Every project of every team the reader is on -- for the board across
    meetings, which filters by project without a meeting to name the team."""
    return [service.project_read(row) for row in projects.reader_projects(session, reader.id)]


@router.get("/teams/mine", response_model=list[TeamRead])
def my_teams(session: SessionDep, reader: CurrentUser) -> list[TeamRead]:
    """The reader's own teams, by name -- for the board across meetings, which
    shows the items team by team and has only each item's ``team_id``."""
    return service.reader_teams(session, reader.id)


@router.get("/sync-log", response_model=SyncLogRead)
def team_sync_log(team_id: str, session: SessionDep, reader: CurrentUser) -> SyncLogRead:
    """S28's "동기화 기록": the copies of the team's items that failed and still
    stand, and the latest that were made. Any member; a calendar row only to
    the person it is about. See ``sync_log``."""
    team = _member_team(session, reader, None, team_id)
    return sync_log.team_sync_log(session, team_id=team, reader_id=reader.id)


@router.get("/projects", response_model=list[ProjectRead])
def list_projects(
    session: SessionDep,
    reader: CurrentUser,
    meeting_id: str | None = None,
    team_id: str | None = None,
) -> list[ProjectRead]:
    """The team's projects, named by one of its meetings or by the team (S28)."""
    team = _member_team(session, reader, meeting_id, team_id)
    return [service.project_read(row) for row in projects.team_projects(session, team)]


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectWrite, team_id: str, session: SessionDep, reader: CurrentUser
) -> ProjectRead:
    """Add a project to the team. Any member, as with the team's integrations."""
    team = _member_team(session, reader, None, team_id)
    row = projects.save_project(
        session,
        team,
        name=payload.name,
        aliases=payload.aliases,
        jira_project_key=payload.jira_project_key,
    )
    response = service.project_read(row)
    session.commit()
    return response


@router.put("/projects/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: str, payload: ProjectWrite, team_id: str, session: SessionDep, reader: CurrentUser
) -> ProjectRead:
    team = _member_team(session, reader, None, team_id)
    row = projects.save_project(
        session,
        team,
        name=payload.name,
        aliases=payload.aliases,
        jira_project_key=payload.jira_project_key,
        project_id=project_id,
    )
    response = service.project_read(row)
    session.commit()
    return response


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, team_id: str, session: SessionDep, reader: CurrentUser) -> None:
    """Delete a project; what was in it becomes 미분류."""
    team = _member_team(session, reader, None, team_id)
    projects.delete_project(session, team, project_id)
    session.commit()


@router.get("/materials", response_model=list[MaterialRead])
def list_materials(team_id: str, session: SessionDep, reader: CurrentUser) -> list[MaterialRead]:
    """The Drive files the team keeps on its 자료 screen (#817), the newest
    first. Members of the team only; anyone else gets the 404 an unknown team
    gets."""
    team = _member_team(session, reader, None, team_id)
    return [materials.read(row) for row in materials.team_materials(session, team)]


@router.post("/materials", response_model=MaterialRead, status_code=status.HTTP_201_CREATED)
def register_material(
    payload: MaterialWrite, team_id: str, session: SessionDep, reader: CurrentUser
) -> MaterialRead:
    """Put a Drive file on the team's shelf: a title and a pasted link. Any
    member, as with the team's projects. Only the file's id is kept, and the
    file itself is never read."""
    team = _member_team(session, reader, None, team_id)
    row = materials.register(session, team, title=payload.title, link=payload.link)
    response = materials.read(row)
    session.commit()
    return response


@router.delete("/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_material(
    material_id: str, team_id: str, session: SessionDep, reader: CurrentUser
) -> None:
    """Take a material off the team's shelf. Any member; the Drive file is not
    touched."""
    team = _member_team(session, reader, None, team_id)
    materials.delete_material(session, team, material_id)
    session.commit()


@router.put("/action-items/{action_item_id}/project", response_model=ActionItemRead)
def place_action_item(
    action_item_id: str, payload: ProjectPlacement, session: SessionDep, reader: CurrentUser
) -> ActionItemRead:
    """Put an item in one of its team's projects, or none."""
    item = service.readable_action_item(session, action_item_id, reader)
    projects.place(session, item, payload.project_id)
    response = service.read_one(session, item)
    session.commit()
    return response


@router.put("/decisions/{decision_id}/project", status_code=status.HTTP_204_NO_CONTENT)
def place_decision(
    decision_id: str, payload: ProjectPlacement, session: SessionDep, reader: CurrentUser
) -> None:
    """Put a decision in one of its team's projects, or none."""
    decision = service.readable_decision(session, decision_id, reader)
    projects.place(session, decision, payload.project_id)
    session.commit()


@router.get("/reviews/{meeting_id}/outbound", response_model=Outbound)
def get_outbound(meeting_id: str, session: SessionDep, reader: CurrentUser) -> Outbound:
    """What of the meeting would go to Notion, Jira or Slack -- confirmed
    decisions and accepted items -- and what is held back. A read: no sync
    asks it before sending, each is checked on its own request
    (``service.outbound_for_meeting``)."""
    service.require_readable_meeting(session, meeting_id, reader)
    return service.outbound_for_meeting(session, meeting_id)


@router.post("/decisions", response_model=ReviewDecision, status_code=status.HTTP_201_CREATED)
def create_decision(
    payload: DecisionCreate, session: SessionDep, reader: CurrentUser, background: BackgroundTasks
) -> ReviewDecision:
    """Add a decision the model missed. It is confirmed and survives a rerun, so
    its Notion page goes out as for any confirmed decision."""
    service.require_readable_meeting(session, payload.meeting_id, reader)
    response = service.create_decision(session, payload)
    session.commit()
    background.add_task(tasks.sync_decision_after_confirmation, response.id)
    return response


@router.delete("/decisions/{decision_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_decision(
    decision_id: str, session: SessionDep, reader: CurrentUser, background: BackgroundTasks
) -> None:
    """Delete a decision a person added; reject one the model proposed.

    The model's would come back on the next run, so rejecting is what keeps it
    gone. See ``service.delete_decision``. Either way a page its confirmation
    made is taken out of Notion after the response (#669), and the project
    minutes that carried it are rewritten without it.
    """
    decision = service.readable_decision(session, decision_id, reader)
    had_page = service.decision_has_page(session, decision_id)
    # Taken before the row goes: a decision a person added is really deleted,
    # and nothing could say afterwards which meeting's minutes to rewrite
    # (#787 review).
    meeting_id = decision.meeting_id
    service.delete_decision(session, decision)
    session.commit()
    if had_page:
        background.add_task(tasks.sync_decision_after_confirmation, decision_id)
    background.add_task(tasks.refresh_project_minutes, meeting_id)


def _member_team(
    session: Session, reader: User, meeting_id: str | None, team_id: str | None = None
) -> str:
    """The team an integration-setup request is about, after checking the caller
    belongs to it -- named by a meeting (the 액션 tab) or by the team itself (S28
    settings, #496). Anyone else gets the 404 an unknown meeting or team gets
    (#189)."""
    if meeting_id:
        service.require_readable_meeting(session, meeting_id, reader)
        found = session.scalar(select(Meeting.team_id).where(Meeting.id == meeting_id))
        assert found is not None  # the check above found it
        return found
    if not team_id or not service.is_team_member(session, team_id, reader.id):
        raise NotFoundError("team", team_id or "")
    return team_id


@router.get("/confirmations", response_model=list[MyConfirmation])
def my_confirmations(
    meeting_id: str, session: SessionDep, reader: CurrentUser
) -> list[MyConfirmation]:
    """The meeting's ambiguous agreements the caller said, to answer on the web
    (#585: a Slack click has no receiver yet). Nobody else's."""
    return service.my_confirmations(session, meeting_id, reader)


@router.post("/confirmations/{utterance_id}", response_model=MyConfirmation)
def answer_confirmation(
    utterance_id: str,
    payload: ConfirmationAnswerIn,
    session: SessionDep,
    reader: CurrentUser,
    background: BackgroundTasks,
) -> MyConfirmation:
    """The caller's answer to one of their own questions -- the DM button's
    path. A commitment makes the draft now and its summary after the commit
    (``tasks.summarise_confirmed_draft``, #577)."""
    response = service.answer_confirmation(session, utterance_id, reader, payload.answer)
    session.commit()
    if payload.answer == "commitment":
        background.add_task(tasks.summarise_confirmed_draft.delay, utterance_id)
    return response


def _reminder_setting(on: bool) -> DueReminderSetting:
    """The person's choice, and which of the messages it governs this
    deployment sends at all."""
    settings = get_settings()
    return DueReminderSetting(
        on=on,
        sent_here=settings.due_reminders,
        weekly_here=settings.weekly_digest,
        daily_here=settings.daily_digest,
        work_report_here=settings.work_report,
        after_meeting_here=settings.after_meeting_notice,
    )


@router.get("/me/due-reminders", response_model=DueReminderSetting)
def my_due_reminders(session: SessionDep, reader: CurrentUser) -> DueReminderSetting:
    """Whether the caller gets due-date reminders by Slack DM. Their own only:
    there is no parameter naming anybody else."""
    return _reminder_setting(service.due_reminders_on(session, reader.id))


@router.put("/me/due-reminders", response_model=DueReminderSetting)
def set_my_due_reminders(
    payload: DueReminderSettingIn, session: SessionDep, reader: CurrentUser
) -> DueReminderSetting:
    """Turn the caller's own due-date reminders on or off (review of #751)."""
    on = service.set_due_reminders(session, reader.id, on=payload.on, now=datetime.now(tz=UTC))
    session.commit()
    return _reminder_setting(on)


def _pause_read(
    session: Session, user_id: str, calendar: leave_calendar.Outcome | None = None
) -> NotificationPauseRead:
    pause = service.notification_pause(session, user_id)
    return NotificationPauseRead(
        starts_on=pause.starts_on if pause is not None else None,
        ends_on=pause.ends_on if pause is not None else None,
        on_calendar=pause is not None and bool(pause.calendar_event_id),
        calendar_leave=get_settings().leave_from_calendar,
        calendar_connected=leave_calendar.connected(session, user_id),
        calendar=calendar,
    )


@router.get("/me/notification-pause", response_model=NotificationPauseRead)
def my_notification_pause(session: SessionDep, reader: CurrentUser) -> NotificationPauseRead:
    """The days the caller asked for no morning DM and no Monday digest.
    Their own only: there is no parameter naming anybody else, and no route
    that shows one person's dates to another."""
    return _pause_read(session, reader.id)


@router.put("/me/notification-pause", response_model=NotificationPauseRead)
def set_my_notification_pause(
    payload: NotificationPause, session: SessionDep, reader: CurrentUser
) -> NotificationPauseRead:
    """Set, replace or -- with both days ``null`` -- clear the caller's own
    pause (the user, 2026-10-05). With ``on_calendar`` the range also goes onto
    the caller's own calendar (2026-10-06) -- ``false`` takes an earlier event
    off, and left out leaves the calendar as it stands -- in the request: they pressed 저장
    and wait to see whether it went. The dates are saved whatever the calendar
    answers, and ``calendar`` in the answer says which it was. The dates are
    committed before Google is asked (``leave_calendar.set_leave``); a save
    that arrives while an earlier one is still at the calendar is refused
    with 409 and changes nothing."""
    outcome = tasks.set_leave(
        session,
        reader.id,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        on_calendar=payload.on_calendar,
        now=datetime.now(tz=UTC),
    )
    session.commit()
    return _pause_read(session, reader.id, outcome)


@router.post("/jira/backfill")
def backfill_jira(
    session: SessionDep,
    reader: CurrentUser,
    meeting_id: str | None = None,
    team_id: str | None = None,
) -> dict[str, int]:
    """Put every confirmed item of this meeting's team into its Jira project now
    -- what the screen calls right after a project is chosen, so a project that
    replaces a deleted one holds everything the old one did (#458). Members of
    the team only: anyone else gets the 404 an unknown meeting gets (#189)."""
    return tasks.backfill_jira(_member_team(session, reader, meeting_id, team_id))


@router.get("/jira/issues", response_model=list[JiraProjectIssues])
def jira_open_issues(
    session: SessionDep,
    reader: CurrentUser,
    meeting_id: str | None = None,
    team_id: str | None = None,
) -> list[JiraProjectIssues]:
    """The open issues of a Jira project a team connected, read from Jira now
    and shown -- never stored (decided with the user, 2026-10-02). Named by a
    meeting or a team, it answers for that team, members only (#189);
    without either, for every team the caller is on, which is what the
    board across meetings needs. A team that never connected Jira is left out."""
    if meeting_id or team_id:
        teams = [_member_team(session, reader, meeting_id, team_id)]
    else:
        teams = service.team_ids_of(session, reader.id)
    found = (jira_issues.project_issues(session, team) for team in teams)
    return [project for project in found if project is not None]


@router.get("/notion/setup")
def notion_setup_state(
    session: SessionDep,
    reader: CurrentUser,
    meeting_id: str | None = None,
    team_id: str | None = None,
) -> dict[str, Any]:
    """After a one-click Notion connection (#428): the pages the team shared with
    Autune, and where its databases are now, if anywhere."""
    return notion_connect.pages_for(_member_team(session, reader, meeting_id, team_id))


@router.post("/notion/setup")
def notion_set_up(
    session: SessionDep,
    reader: CurrentUser,
    meeting_id: str | None = None,
    team_id: str | None = None,
    page_id: str | None = None,
) -> dict[str, Any]:
    """Make Autune's databases under ``page_id`` -- or, with none, in an
    "Autune" page among the connecting person's private pages -- and queue
    filling them with every confirmed action item and decision of the team
    (#428, #481). Notion's own message comes back when it refuses the page."""
    team = _member_team(session, reader, meeting_id, team_id)
    try:
        return notion_connect.set_up(team, page_id)
    except NotionSetupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
