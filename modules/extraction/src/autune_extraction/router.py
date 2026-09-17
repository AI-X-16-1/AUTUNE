"""HTTP entry point for module B.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/extraction`` is applied by apps/api; declare paths relative to it.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_contracts.extraction import ExtractionResult
from autune_core import Meeting, get_session
from autune_core.errors import NotFoundError

from . import service
from .models import ExtActionItem, ExtDecision
from .schemas import (
    ActionItemCreate,
    ActionItemDetail,
    ActionItemRead,
    ActionItemUpdate,
    DecisionReviewUpdate,
    MeetingReview,
    Outbound,
    ReviewDecision,
)

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


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
    response = service.read_model(item)
    session.commit()
    return response


@router.patch("/action-items/{action_item_id}", response_model=ActionItemRead)
def update_action_item(
    action_item_id: str, payload: ActionItemUpdate, session: SessionDep
) -> ActionItemRead:
    """Edit or close an item."""
    item = service.update_action_item(session, _load(session, action_item_id), payload)
    # Before the commit, for the reason ``create_action_item`` gives: an edit
    # answered with a 500 must not also have been saved, or it counts twice
    # in edit cost when the client retries.
    response = service.read_model(item)
    session.commit()
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
    decision_id: str, payload: DecisionReviewUpdate, session: SessionDep
) -> ReviewDecision:
    """Confirm, reject or reword a proposed decision, or put it back to pending."""
    decision = session.get(ExtDecision, decision_id)
    if decision is None:
        raise NotFoundError("decision", decision_id)
    # Built before the commit, for the reason ``create_action_item`` gives.
    response = service.review_decision(session, decision, payload)
    session.commit()
    return response


@router.get("/reviews/{meeting_id}/outbound", response_model=Outbound)
def get_outbound(meeting_id: str, session: SessionDep) -> Outbound:
    """Exactly what "확정해서 보내기" would send: confirmed decisions and accepted items."""
    _meeting(session, meeting_id)
    return service.outbound_for_meeting(session, meeting_id)
