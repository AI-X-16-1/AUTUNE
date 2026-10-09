"""``/api/agent/live/{meeting_id}`` (live-research spec section 5).

The browser relays masked live rows: the live path stores nothing, and module A
may not import this layer. The text is a team member's own input, like a chat
message, and is checked again with ``assert_masked`` before it is queued.

**Covered by the consent attested when the recording starts.** A meeting
recorded with the box unticked is stored, not analysed (``audio.md``), so both
routes refuse it with 409 ``live_research_needs_consent`` before reading a row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent.models import AgentLiveResearch
from autune_audio.tools import consent_attested
from autune_core import CurrentUser, Meeting, SessionDep, TeamMember
from autune_core.errors import NotFoundError
from autune_integrations import assert_masked

from .service import open_document, remaining

router = APIRouter()

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


NEEDS_CONSENT = {"code": "live_research_needs_consent"}


def _masked(rows: list[RowIn]) -> list[dict[str, Any]]:
    """Each row, then all of them joined: what the model is sent.

    A number read with a pause arrives as two rows and neither alone matches
    (audio-live-transcription.md, "A known limit of masking per row"); the
    model gets the rows together, so the joined text must pass too, before
    anything is queued.
    """
    for row in rows:
        assert_masked(row.text, destination="agent_live_research")
    assert_masked(" ".join(r.text for r in rows), destination="agent_live_research")
    return [{"start": r.start, "text": r.text} for r in rows]


QUEUE_EXPIRES_S = 120
"""A queued window is meeting text in the broker. It expires rather than wait
for a worker that is down -- a window minutes old is no use to a live meeting --
so no deletion has to reach into the queue (#1162 review)."""


def enqueue_detect(team_id: str, meeting_id: str, user_id: str, rows: list[dict[str, Any]]) -> None:
    from autune_agent.tasks import live_detect

    live_detect.apply_async(args=(team_id, meeting_id, user_id, rows), expires=QUEUE_EXPIRES_S)


def enqueue_research(document_id: str, context: list[dict[str, Any]], web: bool) -> None:
    from autune_agent.tasks import live_research

    live_research.apply_async(args=(document_id, context, web), expires=QUEUE_EXPIRES_S)


@router.post("/{meeting_id}/detect", status_code=202)
def detect(meeting_id: str, body: DetectIn, user: CurrentUser, session: SessionDep) -> Any:
    meeting = _meeting(session, meeting_id, user.id)
    if not consent_attested(session, meeting.id):
        return JSONResponse(status_code=409, content=NEEDS_CONSENT)
    rows = _masked(body.rows)
    if remaining(session, meeting.id, "auto") == 0:
        return JSONResponse(status_code=429, content={"code": "live_research_cap"})
    enqueue_detect(meeting.team_id, meeting.id, user.id, rows)
    return {"queued": True}


@router.post("/{meeting_id}/research", status_code=202)
def research(meeting_id: str, body: ResearchIn, user: CurrentUser, session: SessionDep) -> Any:
    meeting = _meeting(session, meeting_id, user.id)
    if not consent_attested(session, meeting.id):
        return JSONResponse(status_code=409, content=NEEDS_CONSENT)
    # Context and row in spoken order, checked as one text (`_masked`).
    *context, row = _masked([*body.context, body.row])
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
