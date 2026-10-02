"""The agent layer's HTTP surface, mounted at ``/api/agent``.

- ``POST /chat`` -- one chat turn: route, delegate or ask, answer, record; with the
  proposals the caller may decide.
- ``GET /runs`` -- a team's run timeline, newest first.
- ``GET /research`` -- a meeting's research documents, by who may see which.
- ``GET /pending`` -- L2 proposals waiting for a decision the caller may make.
- ``POST /pending/{id}/approve`` -- approve one; the action runs.
- ``POST /pending/{id}/reject`` -- reject one, with a reason from a fixed list.
- ``GET /approvers`` -- a team's members and the approver scopes each holds.
- ``PUT /approvers/{user_id}`` -- replace one member's scopes (``main/approvers``).

Every route needs a signed-in member of the team it names. The layer answers on
behalf of a team, so a non-member gets 403 rather than someone else's work.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from autune_core import CurrentUser, Meeting, TeamMember, User, get_session
from autune_core.errors import (
    AutuneError,
    ConfigurationError,
    NotFoundError,
    PermissionDeniedError,
    PrivacyViolationError,
)

from .config import get_agent_settings
from .main.actions import collect_actions
from .main.approvers import SCOPE_ORDER, can_manage, list_members, set_scopes
from .main.gemini import GeminiRouter, gemini_tools_from_settings
from .main.own_tools import collect_own_actions, collect_own_tools
from .main.pending import approve, approver_scopes, can_decide, reject
from .main.preview import preview
from .main.registry import collect_tools
from .main.router import Router
from .main.store import run_and_record
from .main.toolcall import ToolModel
from .models import AgentApprover, AgentPendingAction, AgentResearchDocument, AgentRun
from .results import Finding

router = APIRouter()
log = logging.getLogger(__name__)

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


def get_chat_tool_model() -> ToolModel | None:
    """The ask loop's model, or None when the layer is off or has no key. Overridden in tests."""
    return gemini_tools_from_settings()


def _is_member(session: Session, team_id: str, user_id: str) -> bool:
    return (
        session.scalar(
            select(TeamMember.id).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
        is not None
    )


def _require_member(session: Session, team_id: str, user_id: str) -> None:
    if not _is_member(session, team_id, user_id):
        raise PermissionDeniedError("not a member of this team")


class ChatRequest(BaseModel):
    team_id: str | None = None
    """The team asked about. May be left out when ``meeting_id`` is given: the
    meeting names its team, which the shell cannot know for a person in two."""
    meeting_id: str | None = None
    """The meeting the person is looking at (S34), if any. The run is bound to
    it, the way a triggered run is bound to its event's meeting."""
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

    @model_validator(mode="after")
    def _names_a_team(self) -> ChatRequest:
        if self.team_id is None and self.meeting_id is None:
            raise ValueError("team_id or meeting_id is required")
        return self


class ChatMeetingNotFoundError(NotFoundError):
    def __init__(self) -> None:
        # Never the id: it is whatever the caller sent.
        AutuneError.__init__(self, "meeting not found", resource="meeting")


class PendingRead(BaseModel):
    id: str
    team_id: str
    meeting_id: str | None
    subagent: str
    kind: str
    tool: str
    status: str
    reject_reason: str | None
    result_ok: bool | None
    created_at: datetime
    decided_at: datetime | None
    title: str
    body: str
    needs_check: bool
    """Approved, but the action's outcome was never recorded: something raised
    after the claim. It is never re-run; a person checks what happened."""


PREVIEW_FAILED = "미리보기를 만들지 못했습니다"
PENDING_COLUMNS = (
    "id",
    "team_id",
    "meeting_id",
    "subagent",
    "kind",
    "tool",
    "status",
    "reject_reason",
    "result_ok",
    "created_at",
    "decided_at",
)


def _read(session: Session, row: AgentPendingAction) -> PendingRead:
    try:
        shown = preview(session, row, tools={**collect_tools(), **collect_own_tools()})
    except PrivacyViolationError:
        raise
    except Exception as exc:
        # One unreadable row must not hide the others. Log the type only: the
        # message may quote transcript text.
        log.warning("pending preview failed: %s", type(exc).__name__)
        shown = {"title": row.kind, "body": PREVIEW_FAILED}
    fields: dict[str, Any] = {c: getattr(row, c) for c in PENDING_COLUMNS}
    fields.update(shown)
    fields["needs_check"] = row.status == "approved" and row.result_ok is None
    return PendingRead(**fields)


class ChatReply(BaseModel):
    run_id: str
    outcome: str
    route: str | None
    answer: str
    items: list[Finding]
    proposed: int
    """How many actions the subagent proposed, at any level."""
    executed: int
    """How many L1 actions ran and worked -- the "notify after" of section 8.
    An L2 proposal, or one marked L1 whose module declared it L2, is queued for
    approval (``GET /pending``) and not counted here."""
    queued: int = 0
    """How many proposals this run left waiting for an approver -- the rows,
    not ``proposed - executed``, which also counts failed and refused ones."""
    pending: list[PendingRead] = Field(default_factory=list)
    """L2 proposals this run queued that the caller may decide -- an approver
    with the scope, or ``any`` (plan mode's rule). S34 draws 승인 / 거절 for these."""


class RunRead(BaseModel):
    id: str
    route: str | None
    outcome: str
    steps: list[dict[str, Any]]
    proposed: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    """What ran at L1: name, level, ok, reason, evidence ids. Never arguments."""
    latency_ms: int
    requested_by: str | None
    meeting_id: str | None
    created_at: datetime


class ResearchRead(BaseModel):
    id: str
    meeting_id: str
    status: str
    body: str
    created_at: datetime
    decided_at: datetime | None


@router.post("/chat", response_model=ChatReply)
def chat(
    body: ChatRequest,
    user: CurrentUser,
    session: SessionDep,
    chat_router: Annotated[Router, Depends(get_chat_router)],
    tool_model: Annotated[ToolModel | None, Depends(get_chat_tool_model)],
) -> ChatReply:
    team_id = body.team_id
    if body.meeting_id is not None:
        meeting = session.get(Meeting, body.meeting_id)
        # Another team's meeting reads as missing, named team or not.
        if meeting is None or (team_id is not None and meeting.team_id != team_id):
            raise ChatMeetingNotFoundError()
        if team_id is None and not _is_member(session, meeting.team_id, user.id):
            raise ChatMeetingNotFoundError()
        team_id = meeting.team_id
    assert team_id is not None  # ChatRequest requires one of the two
    _require_member(session, team_id, user.id)
    row, state = run_and_record(
        body.message,
        session=session,
        router=chat_router,
        team_id=team_id,
        meeting_id=body.meeting_id,
        requested_by=user.id,
        trigger={"kind": "chat"},
        asker=tool_model,
    )
    scopes = approver_scopes(session, team_id, user.id)
    waiting = session.scalars(
        select(AgentPendingAction).where(
            AgentPendingAction.run_id == row.id, AgentPendingAction.status == "pending"
        )
    ).all()
    outcome = state.get("outcome")
    return ChatReply(
        run_id=row.id,
        outcome=row.outcome,
        route=row.route,
        answer=state.get("answer", ""),
        items=outcome.result.items if outcome else [],
        proposed=len(outcome.proposed) if outcome else 0,
        executed=sum(1 for a in row.actions if a.get("ok")),
        queued=len(waiting),
        pending=[_read(session, r) for r in waiting if can_decide(scopes, r)],
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


@router.get("/research", response_model=list[ResearchRead])
def list_research(
    user: CurrentUser, session: SessionDep, team_id: str, meeting_id: str
) -> list[AgentResearchDocument]:
    """Approved documents for any member; proposals too for a research approver."""
    _require_member(session, team_id, user.id)
    approver = session.scalar(
        select(AgentApprover.user_id).where(
            AgentApprover.team_id == team_id,
            AgentApprover.user_id == user.id,
            AgentApprover.scope.in_(("research", "any")),
        )
    )
    visible = ("approved", "proposed") if approver else ("approved",)
    return list(
        session.scalars(
            select(AgentResearchDocument)
            .where(
                AgentResearchDocument.team_id == team_id,
                AgentResearchDocument.meeting_id == meeting_id,
                AgentResearchDocument.status.in_(visible),
            )
            .order_by(AgentResearchDocument.created_at.desc())
        )
    )


class RejectRequest(BaseModel):
    reason: Literal["wrong_evidence", "not_now", "handled_elsewhere", "other"]


@router.get("/pending", response_model=list[PendingRead])
def list_pending(
    user: CurrentUser, session: SessionDep, team_id: str | None = None
) -> list[PendingRead]:
    """Pending L2 proposals the caller may decide, in every team or in ``team_id``.

    Also an approval that was interrupted -- ``approved`` with no outcome, see
    ``main/pending.approve`` -- marked ``needs_check``, so it is not hidden.
    """
    if team_id is not None:
        _require_member(session, team_id, user.id)
        team_ids = [team_id]
    else:
        team_ids = list(
            session.scalars(
                select(AgentApprover.team_id).where(AgentApprover.user_id == user.id).distinct()
            )
        )
    # approver_scopes counts only current members, so a removed member keeps nothing.
    scopes = {t: approver_scopes(session, t, user.id) for t in team_ids}
    scopes = {t: s for t, s in scopes.items() if s}
    if not scopes:
        return []
    rows = session.scalars(
        select(AgentPendingAction)
        .where(
            AgentPendingAction.team_id.in_(scopes),
            or_(
                AgentPendingAction.status == "pending",
                and_(
                    AgentPendingAction.status == "approved",
                    AgentPendingAction.result_ok.is_(None),
                ),
            ),
        )
        .order_by(AgentPendingAction.created_at.desc())
    ).all()
    return [_read(session, r) for r in rows if can_decide(scopes[r.team_id], r)]


@router.post("/pending/{pending_id}/approve", response_model=PendingRead)
def approve_pending(pending_id: str, user: CurrentUser, session: SessionDep) -> PendingRead:
    # approve() commits its own claim before the action runs; we commit the outcome.
    row = approve(
        session, pending_id, user_id=user.id, actions={**collect_actions(), **collect_own_actions()}
    )
    session.commit()
    return _read(session, row)


@router.post("/pending/{pending_id}/reject", response_model=PendingRead)
def reject_pending(
    pending_id: str, body: RejectRequest, user: CurrentUser, session: SessionDep
) -> PendingRead:
    row = reject(session, pending_id, user_id=user.id, reason=body.reason)
    session.commit()
    return _read(session, row)


ApproverScope = Literal["any", "report", "research", "followup", "workload"]


class ApproverMember(BaseModel):
    user_id: str
    name: str
    scopes: list[str]


class ApproversRead(BaseModel):
    can_manage: bool
    """Whether the caller may change the list: nobody is an approver yet, or
    the caller holds ``any``."""
    scopes: list[str]
    members: list[ApproverMember]


class ApproverScopesWrite(BaseModel):
    scopes: list[ApproverScope] = Field(max_length=len(SCOPE_ORDER))


@router.get("/approvers", response_model=ApproversRead)
def list_approvers(user: CurrentUser, session: SessionDep, team_id: str) -> ApproversRead:
    _require_member(session, team_id, user.id)
    return ApproversRead(
        can_manage=can_manage(session, team_id, user.id),
        scopes=list(SCOPE_ORDER),
        members=[
            ApproverMember(user_id=u, name=n, scopes=s)
            for u, n, s in list_members(session, team_id)
        ],
    )


@router.put("/approvers/{user_id}", response_model=ApproverMember)
def put_approver(
    user_id: str,
    body: ApproverScopesWrite,
    user: CurrentUser,
    session: SessionDep,
    team_id: str,
) -> ApproverMember:
    _require_member(session, team_id, user.id)
    scopes = set_scopes(session, team_id=team_id, user_id=user_id, scopes=body.scopes, by=user.id)
    session.commit()
    name = session.scalar(select(User.display_name).where(User.id == user_id)) or ""
    return ApproverMember(user_id=user_id, name=name, scopes=scopes)
