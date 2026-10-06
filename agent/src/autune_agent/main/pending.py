"""Plan mode's queue (agent/docs/specs/2026-09-30-plan-mode-design.md).

An L2 proposal becomes an ``agent_pending_actions`` row and waits for a person
with the right scope. Its arguments must be ids and short scalars: text a
proposal needs lives in the owning store first and is pointed at by id, so the
row holds nothing a meeting deletion could miss. A proposal's ``kind`` and
``tool`` are already code names (``ProposedAction`` refuses anything else);
this module checks the arguments and the run's subagent name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.orm import Session

from autune_agent.models import REJECT_REASONS, AgentApprover, AgentPendingAction, AgentRun
from autune_agent.results import ProposedAction
from autune_core import Meeting, TeamMember
from autune_core.errors import AutuneError, ConflictError, NotFoundError, PermissionDeniedError

from .actions import (
    ARGUMENT_REFUSED,
    NOT_DECLARED,
    OUT_OF_SCOPE,
    Action,
    own_reason,
    run_action,
)
from .registry import RunScope

_ID = re.compile(r"[a-z]+_[A-Za-z0-9]+")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_ENUM = re.compile(r"[a-z_]{1,32}")
_SUBAGENT = re.compile(r"[a-z_]{0,32}")

SCOPES = {
    "research": "research",
    "workload": "workload",
    "followup": "followup",
    "report": "report",
}


MEETING_NOT_FOUND = OUT_OF_SCOPE[1]
"""The proposal named a meeting outside the run's team; the id is not echoed."""


def arguments_ok(arguments: Mapping[str, Any]) -> bool:
    """Ids, ISO dates, booleans and short lowercase enums; nothing else."""
    for key, value in arguments.items():
        if not _ENUM.fullmatch(key):
            return False
        if isinstance(value, bool):
            continue
        if not isinstance(value, str):
            return False
        if not (_ID.fullmatch(value) or _DATE.fullmatch(value) or _ENUM.fullmatch(value)):
            return False
    return True


def scope_for(subagent: str | None) -> str:
    return SCOPES.get(subagent or "", "any")


def _needs_approval(proposal: ProposedAction, actions: Mapping[str, Action]) -> bool:
    """L2 by the proposal, or by the level its module declared (a proposal cannot demote)."""
    if proposal.level == "L2":
        return True
    action = actions.get(proposal.tool)
    return action is not None and action.level == "L2"


def queue_l2(
    session: Session,
    *,
    run: AgentRun,
    proposed: Sequence[ProposedAction],
    actions: Mapping[str, Action],
    team_wide: bool = False,
) -> list[dict[str, Any]]:
    """Queue the L2 proposals of ``run``; return a record for each refused one.

    A proposal is L2 when it says so or when its module declared the action L2.
    ``actions`` is the mapping ``execute_l1`` was given, so a proposal marked L1
    that ``execute_l1`` kept for approval is queued here rather than dropped.

    The new rows supersede the same subagent's earlier pending rows for the
    team: those about the same meeting, or, when ``team_wide`` (the subagent's
    ``proposals_per == "team"``), all of them whatever meeting they came from.
    A team-wide judgment replaces the last one whoever asked, so one item is
    never left proposed to two people (#636). A chat about a meeting replaces
    an earlier chat's rows, and the pipeline's rows only for the same action:
    "다시 써줘" or "올려줘" proposes the post the pipeline already left, and
    keeping both leaves a dead or doubled card (#879). Someone asking on a
    meeting page still never retires the pipeline's proposal of another action
    (#651 review).

    A run about no meeting (a chat on the team screen) whose proposal names one
    in ``meeting_id`` is treated as about that meeting: the row is that
    meeting's, so asking again replaces the last card instead of stacking
    another, and approval runs under it (#862). A meeting outside the team is
    refused here rather than queued.
    """
    refused: list[dict[str, Any]] = []
    subagent = run.route or ""
    for proposal in proposed:
        if not _needs_approval(proposal, actions):
            continue
        if not (_SUBAGENT.fullmatch(subagent) and arguments_ok(proposal.arguments)):
            refused.append(
                {
                    "tool": proposal.tool,
                    "level": "L2",
                    "ok": False,
                    "reason": ARGUMENT_REFUSED,
                    "evidence": list(proposal.evidence),
                }
            )
            continue
        meeting_id = run.meeting_id or proposal.arguments.get("meeting_id")
        if meeting_id is not None and run.meeting_id is None:
            meeting = session.get(Meeting, meeting_id)
            if meeting is None or meeting.team_id != run.team_id:
                refused.append(
                    {
                        "tool": proposal.tool,
                        "level": "L2",
                        "ok": False,
                        "reason": MEETING_NOT_FOUND,
                        "evidence": list(proposal.evidence),
                    }
                )
                continue
        from_chat = (run.trigger or {}).get("kind") == "chat"
        if team_wide or meeting_id is not None:
            narrower: tuple[Any, ...] = ()
            if not team_wide:
                narrower = (AgentPendingAction.meeting_id == meeting_id,)
                if from_chat:
                    chat_runs = select(AgentRun.id).where(
                        AgentRun.trigger["kind"].as_string() == "chat"
                    )
                    narrower += (
                        or_(
                            AgentPendingAction.run_id.in_(chat_runs),
                            AgentPendingAction.tool == proposal.tool,
                        ),
                    )
            session.execute(
                update(AgentPendingAction)
                .where(
                    AgentPendingAction.team_id == run.team_id,
                    *narrower,
                    AgentPendingAction.subagent == subagent,
                    AgentPendingAction.status == "pending",
                    # One run's proposals never supersede each other; a row whose
                    # run was deleted (run_id NULL) is older than any run.
                    or_(AgentPendingAction.run_id.is_(None), AgentPendingAction.run_id != run.id),
                )
                .values(status="superseded")
            )
        session.add(
            AgentPendingAction(
                team_id=run.team_id,
                meeting_id=meeting_id,
                run_id=run.id,
                subagent=subagent,
                tool=proposal.tool,
                kind=proposal.kind,
                arguments=dict(proposal.arguments),
                evidence=list(proposal.evidence),
                scope=scope_for(run.route),
            )
        )
        session.flush()
    return refused


class PendingNotFoundError(NotFoundError):
    def __init__(self) -> None:
        # Never the id: it is whatever the caller sent. Skip NotFoundError's
        # formatting so neither ``str()`` nor ``message`` can carry it.
        AutuneError.__init__(self, "pending action not found", resource="pending action")


class PendingDecidedError(ConflictError):
    def __init__(self) -> None:
        super().__init__("this proposal was already decided")


class NotAnApproverError(PermissionDeniedError):
    def __init__(self) -> None:
        super().__init__("not an approver for this proposal")


def approver_scopes(session: Session, team_id: str, user_id: str) -> set[str]:
    return set(
        session.scalars(
            select(AgentApprover.scope)
            .join(
                TeamMember,
                (TeamMember.team_id == AgentApprover.team_id)
                & (TeamMember.user_id == AgentApprover.user_id),
            )
            .where(AgentApprover.team_id == team_id, AgentApprover.user_id == user_id)
        )
    )


def can_decide(scopes: set[str], row: AgentPendingAction) -> bool:
    return "any" in scopes or row.scope in scopes


def _load(session: Session, pending_id: str, user_id: str) -> AgentPendingAction:
    row = session.get(AgentPendingAction, pending_id)
    if row is None:
        raise PendingNotFoundError()
    scopes = approver_scopes(session, row.team_id, user_id)
    if not scopes:
        raise PendingNotFoundError()  # another team's row reads as missing
    if not can_decide(scopes, row):
        raise NotAnApproverError()
    return row


def _claim(session: Session, row: AgentPendingAction, user_id: str, **values: Any) -> None:
    """Move a row out of ``pending`` exactly once; a concurrent decider loses."""
    outcome = cast(
        CursorResult[Any],
        session.execute(
            update(AgentPendingAction)
            .where(AgentPendingAction.id == row.id, AgentPendingAction.status == "pending")
            .values(decided_by=user_id, decided_at=datetime.now(UTC), **values)
        ),
    )
    if outcome.rowcount != 1:
        session.rollback()
        raise PendingDecidedError()
    session.commit()  # the claim outlives whatever happens to the action below
    session.refresh(row)


def approve(
    session: Session, pending_id: str, *, user_id: str, actions: Mapping[str, Action]
) -> AgentPendingAction:
    """Run the proposal under its own run's scope; the outcome refines the status.

    Execution is at-most-once. The claim is committed before the action runs,
    because other modules' actions commit in their own sessions: were the claim
    to roll back after the action committed, the row would read ``pending`` and
    a second approval would repeat a write that moves a person. A raise or a
    crash after the claim leaves ``approved`` with ``result_ok`` unset -- visible,
    never re-run. The caller commits the outcome.
    """
    row = _load(session, pending_id, user_id)
    _claim(session, row, user_id, status="approved")
    action = actions.get(row.tool)
    if action is None:
        row.status, row.result_ok, row.result_reason = "failed", False, NOT_DECLARED
    else:
        result = run_action(
            action,
            row.arguments,
            session=session,
            # The approver answers for the decision, so a write that records
            # who made it records them, not the requester (#862).
            scope=RunScope(team_id=row.team_id, meeting_id=row.meeting_id, user_id=user_id),
        )
        row.result_ok = result.ok
        row.status = "approved" if result.ok else "failed"
        row.result_reason = None if result.ok else own_reason(result.reason)
    session.flush()
    return row


def reject(session: Session, pending_id: str, *, user_id: str, reason: str) -> AgentPendingAction:
    if reason not in REJECT_REASONS:
        raise ValueError("reason must be one of " + ", ".join(REJECT_REASONS))
    row = _load(session, pending_id, user_id)
    _claim(session, row, user_id, status="rejected", reject_reason=reason)
    return row
