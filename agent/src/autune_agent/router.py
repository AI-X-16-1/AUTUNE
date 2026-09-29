"""The agent layer's HTTP surface, mounted at ``/api/agent``.

- ``POST /chat`` -- one chat turn: route, delegate, answer, record.
- ``GET /runs`` -- a team's run timeline, newest first.

Every route needs a signed-in member of the team it names. The layer answers on
behalf of a team, so a non-member gets 403 rather than someone else's work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_core import CurrentUser, TeamMember, get_session
from autune_core.errors import ConfigurationError, PermissionDeniedError

from .config import get_agent_settings
from .main.gemini import GeminiRouter
from .main.router import Router
from .main.store import run_and_record
from .models import AgentRun
from .results import Finding

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]

MAX_MESSAGE_CHARS = 1000
"""A chat turn is a question, not a pasted transcript."""


def get_chat_router() -> Router:
    """The model behind routing and composing. Overridden in tests."""
    settings = get_agent_settings()
    if settings.router_impl == "off" or not settings.llm_api_key:
        raise ConfigurationError("the agent layer is off or AUTUNE_AGENT_LLM_API_KEY is unset")
    return GeminiRouter(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout_sec=settings.llm_timeout_sec,
    )


def _require_member(session: Session, team_id: str, user_id: str) -> None:
    member = session.scalar(
        select(TeamMember.id).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    if member is None:
        raise PermissionDeniedError("not a member of this team")


class ChatRequest(BaseModel):
    team_id: str
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatReply(BaseModel):
    run_id: str
    outcome: str
    route: str | None
    answer: str
    items: list[Finding]
    proposed: int
    """How many actions the subagent proposed. None runs until plan mode exists."""


class RunRead(BaseModel):
    id: str
    route: str | None
    outcome: str
    steps: list[dict[str, Any]]
    proposed: list[dict[str, Any]]
    latency_ms: int
    requested_by: str | None
    meeting_id: str | None
    created_at: datetime


@router.post("/chat", response_model=ChatReply)
def chat(
    body: ChatRequest,
    user: CurrentUser,
    session: SessionDep,
    chat_router: Annotated[Router, Depends(get_chat_router)],
) -> ChatReply:
    _require_member(session, body.team_id, user.id)
    row, state = run_and_record(
        body.message,
        session=session,
        router=chat_router,
        team_id=body.team_id,
        requested_by=user.id,
        trigger={"kind": "chat"},
    )
    outcome = state.get("outcome")
    return ChatReply(
        run_id=row.id,
        outcome=row.outcome,
        route=row.route,
        answer=state.get("answer", ""),
        items=outcome.result.items if outcome else [],
        proposed=len(outcome.proposed) if outcome else 0,
    )


@router.get("/runs", response_model=list[RunRead])
def list_runs(
    user: CurrentUser,
    session: SessionDep,
    team_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[AgentRun]:
    _require_member(session, team_id, user.id)
    return list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.team_id == team_id)
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
        )
    )
