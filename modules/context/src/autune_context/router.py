"""HTTP entry point for module D.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/context`` is applied by apps/api; declare paths relative to it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from autune_core import get_session

from . import service
from .schemas import (
    DecisionLineageRead,
    DecisionSummaryRead,
    DecisionVersionRead,
    LinkConfirmRequest,
    TopicLinkRead,
    TopicLinksRead,
)

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "context", "status": "ok"}


@router.get("/links/{meeting_id}", response_model=TopicLinksRead)
def get_links(meeting_id: str, session: SessionDep) -> TopicLinksRead:
    """This meeting's topic links, asserted (incl. user-confirmed) and pending."""
    asserted, pending = service.get_topic_links(session, meeting_id)
    return TopicLinksRead(
        asserted=[TopicLinkRead.model_validate(link) for link in asserted],
        pending=[TopicLinkRead.model_validate(link) for link in pending],
    )


@router.post("/links/{link_id}/confirm", response_model=TopicLinkRead)
def confirm_link(
    link_id: int, payload: LinkConfirmRequest, session: SessionDep
) -> TopicLinkRead:
    """A user confirms or rejects a pending link."""
    link = service.confirm_topic_link(session, link_id, payload.status)
    session.commit()
    return TopicLinkRead.model_validate(link)


@router.get("/decisions/{thread_id}", response_model=DecisionLineageRead)
def get_decision_thread(thread_id: str, session: SessionDep) -> DecisionLineageRead:
    """A decision thread's full lineage timeline, oldest version first."""
    thread, versions = service.get_decision_lineage(session, thread_id)
    return DecisionLineageRead(
        thread_id=thread.id,
        topic_label=thread.topic_label,
        versions=[DecisionVersionRead.model_validate(version) for version in versions],
    )


@router.get("/decisions", response_model=list[DecisionSummaryRead])
def list_decision_threads(
    team_id: str,
    session: SessionDep,
    topic: str | None = None,
    change_type: str | None = None,
) -> list[DecisionSummaryRead]:
    """A team's decision threads by their current head, filterable by topic
    and/or change type."""
    pairs = service.list_decisions(session, team_id, topic=topic, change_type=change_type)
    return [
        DecisionSummaryRead(
            thread_id=thread.id,
            topic_label=thread.topic_label,
            meeting_id=version.meeting_id,
            change_type=version.change_type,
            confidence=version.confidence,
            updated_at=version.updated_at,
        )
        for thread, version in pairs
    ]
