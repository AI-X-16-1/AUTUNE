"""HTTP entry point for module B.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/extraction`` is applied by apps/api; declare paths relative to it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from autune_core import get_session
from autune_core.errors import NotFoundError

from . import service
from .models import ExtActionItem
from .schemas import ActionItemCreate, ActionItemRead, ActionItemUpdate

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


@router.post("/action-items", response_model=ActionItemRead, status_code=status.HTTP_201_CREATED)
def create_action_item(payload: ActionItemCreate, session: SessionDep) -> ExtActionItem:
    """Add an item the model missed.

    ADR 0006 ranks recall above precision because a wrong item costs a click and
    a missing one costs re-reading the meeting. This is the route that makes the
    second recoverable.
    """
    item = service.create_action_item(session, payload)
    session.commit()
    return item


@router.patch("/action-items/{action_item_id}", response_model=ActionItemRead)
def update_action_item(
    action_item_id: str, payload: ActionItemUpdate, session: SessionDep
) -> ExtActionItem:
    """Edit or close an item."""
    item = service.update_action_item(session, _load(session, action_item_id), payload)
    session.commit()
    return item


@router.delete("/action-items/{action_item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_action_item(action_item_id: str, session: SessionDep) -> None:
    """Delete an item the model got wrong.

    Real deletion. ``privacy.md`` allows no soft deletes and no tombstones
    holding content; the edit-cost counter records that it happened without
    keeping what was deleted.
    """
    service.delete_action_item(session, _load(session, action_item_id))
    session.commit()
