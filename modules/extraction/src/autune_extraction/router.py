"""HTTP entry point for module B.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/extraction`` is applied by apps/api; declare paths relative to it.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_contracts.extraction import ExtractionResult
from autune_core import Meeting, get_session
from autune_core.errors import NotFoundError
from autune_core.settings import get_settings as get_core_settings

from . import service, tasks
from .models import ExtActionItem, ExtDecision
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

# A local-only page for connecting Notion/Slack by hand, until S28 exists.
# It has no auth, so it is mounted nowhere but a developer's machine -- see
# ``dev/routes.py``.
if get_core_settings().env == "local":
    from .dev import router as dev_router

    router.include_router(dev_router, prefix="/dev")


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "extraction", "status": "ok"}


def _load(session: Session, action_item_id: str) -> ExtActionItem:
    """The item, or a 404 that names no meeting content.

    ``AutuneError`` is mapped to a response by apps/api, so the message reaches a
    user and a log. It carries the id and nothing else.
    """
    item = session.get(ExtActionItem, action_item_id)
    if item is None:
        raise NotFoundError("action item", action_item_id)
    return item


@router.get("/results/{meeting_id}", response_model=ExtractionResult)
def get_results(meeting_id: str, session: SessionDep) -> ExtractionResult:
    """Everything this meeting produced, including every correction since.

    A meeting with nothing extracted yet answers with empty lists, not a 404:
    the meeting exists and has, so far, produced nothing. Only a meeting that
    does not exist is not found.
    """
    if session.get(Meeting, meeting_id) is None:
        raise NotFoundError("meeting", meeting_id)
    return service.result_for_meeting(session, meeting_id)


@router.get("/action-items", response_model=list[ActionItemRead])
def list_action_items(
    session: SessionDep,
    meeting_id: str | None = None,
    assignee_id: str | None = None,
    # Aliased so the parameter does not shadow ``fastapi.status`` in this module.
    status_filter: Annotated[ActionStatus | None, Query(alias="status")] = None,
    due_before: date | None = None,
) -> list[ActionItemRead]:
    """Items for the board, by any combination of the four filters."""
    return service.list_action_items(
        session,
        meeting_id=meeting_id,
        assignee_id=assignee_id,
        status=status_filter,
        due_before=due_before,
    )


@router.get("/action-items/{action_item_id}", response_model=ActionItemDetail)
def get_action_item(action_item_id: str, session: SessionDep) -> ActionItemDetail:
    """One item and the text of the utterances it came from, for the drawer."""
    return service.read_detail(session, _load(session, action_item_id))


@router.post("/action-items", response_model=ActionItemRead, status_code=status.HTTP_201_CREATED)
def create_action_item(payload: ActionItemCreate, session: SessionDep) -> ActionItemRead:
    """Add an item the model missed.

    ADR 0006 ranks recall above precision because a wrong item costs a click and
    a missing one costs re-reading the meeting. This is the route that makes the
    second recoverable.
    """
    item = service.create_action_item(session, payload)
    # The response is built before the commit. ``read_model`` reads the
    # candidate threshold, and a threshold that does not parse (a 7 in .env)
    # used to fail here after the item was already saved: the client got a 500
    # for a write that had happened, and a retry made a second item. Failing
    # first lets ``get_session`` roll it back.
    names = service.assignee_names(session, [item])
    name = names.get(item.assignee_id) if item.assignee_id else None
    response = service.read_model(item, assignee_name=name)
    session.commit()
    return response


@router.patch("/action-items/{action_item_id}", response_model=ActionItemRead)
def update_action_item(
    action_item_id: str,
    payload: ActionItemUpdate,
    session: SessionDep,
    background: BackgroundTasks,
) -> ActionItemRead:
    """Edit or close an item. Confirming it queues its Notion page (#30); an
    edit to an already-confirmed item queues an update to the same page."""
    item = _load(session, action_item_id)
    item = service.update_action_item(session, item, payload)
    # Before the commit, for the reason ``create_action_item`` gives: an edit
    # answered with a 500 must not also have been saved, or it counts twice
    # in edit cost when the client retries.
    names = service.assignee_names(session, [item])
    name = names.get(item.assignee_id) if item.assignee_id else None
    response = service.read_model(item, assignee_name=name)
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
def delete_action_item(action_item_id: str, session: SessionDep) -> None:
    """Delete an item the model got wrong.

    Real deletion. ``privacy.md`` allows no soft deletes and no tombstones
    holding content; the edit-cost counter records that it happened without
    keeping what was deleted.
    """
    service.delete_action_item(session, _load(session, action_item_id))
    session.commit()


def _meeting(session: Session, meeting_id: str) -> None:
    if session.get(Meeting, meeting_id) is None:
        raise NotFoundError("meeting", meeting_id)


@router.get("/reviews/{meeting_id}", response_model=MeetingReview)
def get_review(meeting_id: str, session: SessionDep) -> MeetingReview:
    """What needs a person in this meeting before anything is sent (S15, #246)."""
    _meeting(session, meeting_id)
    return service.review_for_meeting(session, meeting_id)


@router.patch("/decisions/{decision_id}", response_model=ReviewDecision)
def review_decision(
    decision_id: str,
    payload: DecisionReviewUpdate,
    session: SessionDep,
    background: BackgroundTasks,
) -> ReviewDecision:
    """Confirm, reject or reword a proposed decision, or put it back to pending.

    Confirming it sends its Notion page (#30); rewording an already-confirmed
    decision updates the same page instead of leaving it stale."""
    decision = session.get(ExtDecision, decision_id)
    if decision is None:
        raise NotFoundError("decision", decision_id)
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
def get_outbound(meeting_id: str, session: SessionDep) -> Outbound:
    """Exactly what confirm-and-send would send: confirmed decisions and accepted items."""
    _meeting(session, meeting_id)
    return service.outbound_for_meeting(session, meeting_id)


@router.post("/decisions", response_model=ReviewDecision, status_code=status.HTTP_201_CREATED)
def create_decision(
    payload: DecisionCreate, session: SessionDep, background: BackgroundTasks
) -> ReviewDecision:
    """Add a decision the model missed. It is confirmed and survives a rerun, so
    its Notion page goes out as for any confirmed decision."""
    response = service.create_decision(session, payload)
    session.commit()
    background.add_task(tasks.sync_decision_after_confirmation, response.id)
    return response


@router.delete("/decisions/{decision_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_decision(decision_id: str, session: SessionDep) -> None:
    """Delete a decision a person added; reject one the model proposed.

    The model's would come back on the next run, so rejecting is what keeps it
    gone. See ``service.delete_decision``.
    """
    decision = session.get(ExtDecision, decision_id)
    if decision is None:
        raise NotFoundError("decision", decision_id)
    service.delete_decision(session, decision)
    session.commit()
