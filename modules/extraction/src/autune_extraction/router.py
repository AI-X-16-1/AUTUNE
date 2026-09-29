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

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_contracts.extraction import ExtractionResult
from autune_core import CurrentUser, Meeting, User, get_session
from autune_core.settings import get_settings as get_core_settings

from . import notion_connect, service, tasks
from .config import get_settings
from .notion_setup import NotionSetupError
from .schemas import (
    ActionItemCreate,
    ActionItemDetail,
    ActionItemRead,
    ActionItemUpdate,
    DecisionCreate,
    DecisionReviewUpdate,
    MeetingReview,
    Outbound,
    ReviewDecision,
)

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


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


@router.get("/action-items/{action_item_id}", response_model=ActionItemDetail)
def get_action_item(
    action_item_id: str, session: SessionDep, reader: CurrentUser
) -> ActionItemDetail:
    """One item and the text of the utterances it came from, for the drawer."""
    item = service.readable_action_item(session, action_item_id, reader)
    return service.read_detail(session, item)


@router.post("/action-items", response_model=ActionItemRead, status_code=status.HTTP_201_CREATED)
def create_action_item(
    payload: ActionItemCreate, session: SessionDep, reader: CurrentUser
) -> ActionItemRead:
    """Add an item the model missed.

    ADR 0006 ranks recall above precision because a wrong item costs a click and
    a missing one costs re-reading the meeting. This is the route that makes the
    second recoverable.
    """
    service.require_readable_meeting(session, payload.meeting_id, reader)
    item = service.create_action_item(session, payload)
    # The response is built before the commit. ``read_model`` reads the
    # candidate threshold, and a threshold that does not parse (a 7 in .env)
    # used to fail here after the item was already saved: the client got a 500
    # for a write that had happened, and a retry made a second item. Failing
    # first lets ``get_session`` roll it back.
    response = service.read_one(session, item)
    session.commit()
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
    edit to an already-confirmed item queues an update to the same page."""
    item = service.readable_action_item(session, action_item_id, reader)
    item = service.update_action_item(session, item, payload)
    # Before the commit, for the reason ``create_action_item`` gives: an edit
    # answered with a 500 must not also have been saved, or it counts twice
    # in edit cost when the client retries.
    response = service.read_one(session, item)
    session.commit()
    # After the response, so the sync reads the committed row and the board is
    # not held on Notion. Confirming or any later edit both queue the same
    # task -- ``sync_action_item_to_notion`` itself decides create vs. update
    # from whether the claim already exists, so a still-``needs_confirmation``
    # item is the only case this need not queue at all.
    if item.status != ActionStatus.NEEDS_CONFIRMATION.value:
        background.add_task(tasks.sync_after_confirmation, item.id)
    return response


@router.delete("/action-items/{action_item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_action_item(action_item_id: str, session: SessionDep, reader: CurrentUser) -> None:
    """Delete an item the model got wrong.

    Real deletion. ``privacy.md`` allows no soft deletes and no tombstones
    holding content; the edit-cost counter records that it happened without
    keeping what was deleted.

    Its due-date event comes off its assignee's calendar first
    (``tasks.remove_calendar_event``, #435), its Jira issue is closed with a
    note (``tasks.close_jira_issue``, #82) and its Notion page goes to Notion's
    trash (``tasks.trash_notion_page``, #467): once the rows cascade away none
    of them can be found again.
    """
    item = service.readable_action_item(session, action_item_id, reader)
    tasks.remove_calendar_event(item.id)
    tasks.close_jira_issue(item.id)
    tasks.trash_notion_page(item.id)
    service.delete_action_item(session, item)
    session.commit()


@router.get("/reviews/{meeting_id}", response_model=MeetingReview)
def get_review(meeting_id: str, session: SessionDep, reader: CurrentUser) -> MeetingReview:
    """What needs a person in this meeting before anything is sent (S15, #246)."""
    service.require_readable_meeting(session, meeting_id, reader)
    return service.review_for_meeting(session, meeting_id)


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
    decision updates the same page instead of leaving it stale."""
    decision = service.readable_decision(session, decision_id, reader)
    # Built before the commit, for the reason ``create_action_item`` gives.
    response = service.review_decision(session, decision, payload)
    session.commit()
    # Confirming or any later reword both queue the same task --
    # ``sync_decision_to_notion`` decides create vs. update from whether the
    # claim already exists.
    if response.status == "confirmed":
        background.add_task(tasks.sync_decision_after_confirmation, decision_id)
    return response


@router.get("/reviews/{meeting_id}/outbound", response_model=Outbound)
def get_outbound(meeting_id: str, session: SessionDep, reader: CurrentUser) -> Outbound:
    """Exactly what confirm-and-send would send: confirmed decisions and accepted items."""
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
def delete_decision(decision_id: str, session: SessionDep, reader: CurrentUser) -> None:
    """Delete a decision a person added; reject one the model proposed.

    The model's would come back on the next run, so rejecting is what keeps it
    gone. See ``service.delete_decision``.
    """
    decision = service.readable_decision(session, decision_id, reader)
    service.delete_decision(session, decision)
    session.commit()


def _member_team(session: Session, reader: User, meeting_id: str) -> str:
    """The team of a meeting the caller belongs to -- the check every
    integration-setup route shares. Anyone else gets the 404 an unknown meeting
    gets (#189)."""
    service.require_readable_meeting(session, meeting_id, reader)
    team_id = session.scalar(select(Meeting.team_id).where(Meeting.id == meeting_id))
    assert team_id is not None  # the check above found it
    return team_id


@router.post("/jira/backfill")
def backfill_jira(meeting_id: str, session: SessionDep, reader: CurrentUser) -> dict[str, int]:
    """Put every confirmed item of this meeting's team into its Jira project now
    -- what the screen calls right after a project is chosen, so a project that
    replaces a deleted one holds everything the old one did (#458). Members of
    the team only: anyone else gets the 404 an unknown meeting gets (#189)."""
    return tasks.backfill_jira(_member_team(session, reader, meeting_id))


@router.get("/notion/setup")
def notion_setup_state(
    meeting_id: str, session: SessionDep, reader: CurrentUser
) -> dict[str, Any]:
    """After a one-click Notion connection (#428): the pages the team shared with
    Autune, and where its databases are now, if anywhere."""
    return notion_connect.pages_for(_member_team(session, reader, meeting_id))


@router.post("/notion/setup")
def notion_set_up(
    meeting_id: str, page_id: str, session: SessionDep, reader: CurrentUser
) -> dict[str, Any]:
    """Make Autune's databases under ``page_id`` and fill them with every
    confirmed action item and decision of the team (#428). Notion's own message
    comes back when it refuses the page."""
    team_id = _member_team(session, reader, meeting_id)
    try:
        return notion_connect.set_up(team_id, page_id)
    except NotionSetupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
