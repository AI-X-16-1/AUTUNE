"""``/api/agent/live/{meeting_id}`` (live-research spec section 5).

The browser relays masked live rows: the live path stores nothing, and module A
may not import this layer. The text is a team member's own input, like a chat
message, and is checked again with ``assert_masked`` before it is queued.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent.models import AgentLiveResearch
from autune_core import CurrentUser, Meeting, TeamMember, get_session
from autune_core.errors import NotFoundError
from autune_integrations import assert_masked

from .service import open_document, remaining

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]

MAX_ROWS = 12
MAX_CONTEXT = 4
MAX_ROW_CHARS = 400


class RowIn(BaseModel):
    start: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=MAX_ROW_CHARS)


class DetectIn(BaseModel):
    rows: list[RowIn] = Field(min_length=1, max_length=MAX_ROWS)


class ResearchIn(BaseModel):
    row: RowIn
    context: list[RowIn] = Field(default_factory=list, max_length=MAX_CONTEXT)


class LiveDocumentRead(BaseModel):
    id: str
    origin: str
    status: str
    question: str
    body: str | None
    web_sources: list[dict[str, str]]
    meeting_sources: list[dict[str, str]]
    created_at: datetime

    model_config = {"from_attributes": True}


def _meeting(session: Session, meeting_id: str, user_id: str) -> Meeting:
    """The meeting, for a current member of its team; 404 for anyone else (#437)."""
    meeting = session.get(Meeting, meeting_id)
    member = meeting is not None and session.scalar(
        select(TeamMember.id).where(
            TeamMember.team_id == meeting.team_id, TeamMember.user_id == user_id
        )
    )
    if meeting is None or not member:
        raise NotFoundError("meeting", meeting_id)
    return meeting


def _masked(rows: list[RowIn]) -> list[dict[str, Any]]:
    for row in rows:
        assert_masked(row.text, destination="agent_live_research")
    return [{"start": r.start, "text": r.text} for r in rows]


def enqueue_detect(team_id: str, meeting_id: str, user_id: str, rows: list[dict[str, Any]]) -> None:
    from autune_agent.tasks import live_detect

    live_detect.delay(team_id, meeting_id, user_id, rows)


def enqueue_research(document_id: str, context: list[dict[str, Any]], web: bool) -> None:
    from autune_agent.tasks import live_research

    live_research.delay(document_id, context, web)


@router.post("/{meeting_id}/detect", status_code=202)
def detect(meeting_id: str, body: DetectIn, user: CurrentUser, session: SessionDep) -> Any:
    meeting = _meeting(session, meeting_id, user.id)
    rows = _masked(body.rows)
    if remaining(session, meeting.id, "auto") == 0:
        return JSONResponse(status_code=429, content={"code": "live_research_cap"})
    enqueue_detect(meeting.team_id, meeting.id, user.id, rows)
    return {"queued": True}


@router.post("/{meeting_id}/research", status_code=202)
def research(meeting_id: str, body: ResearchIn, user: CurrentUser, session: SessionDep) -> Any:
    meeting = _meeting(session, meeting_id, user.id)
    row = _masked([body.row])[0]
    context = _masked(body.context)
    doc = open_document(
        session,
        team_id=meeting.team_id,
        meeting_id=meeting.id,
        user_id=user.id,
        origin="manual",
        question=row["text"],
    )
    if doc is None:
        return JSONResponse(status_code=409, content={"code": "live_research_known_or_full"})
    enqueue_research(doc.id, context, True)
    return {"id": doc.id}


@router.get("/{meeting_id}/documents", response_model=list[LiveDocumentRead])
def documents(meeting_id: str, user: CurrentUser, session: SessionDep) -> list[AgentLiveResearch]:
    meeting = _meeting(session, meeting_id, user.id)
    return list(
        session.scalars(
            select(AgentLiveResearch)
            .where(AgentLiveResearch.meeting_id == meeting.id)
            .order_by(AgentLiveResearch.created_at.desc())
        )
    )
