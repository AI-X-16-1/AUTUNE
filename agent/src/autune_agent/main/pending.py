"""Plan mode's queue (agent/docs/specs/2026-09-30-plan-mode-design.md).

An L2 proposal becomes an ``agent_pending_actions`` row and waits for a person
with the right scope. Its arguments must be ids and short scalars: text a
proposal needs lives in the owning store first and is pointed at by id, so the
row holds nothing a meeting deletion could miss.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from autune_agent.models import AgentPendingAction, AgentRun
from autune_agent.results import ProposedAction

from .actions import ARGUMENT_REFUSED

_ID = re.compile(r"[a-z]+_[A-Za-z0-9]+")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_ENUM = re.compile(r"[a-z_]{1,32}")

SCOPES = {
    "research": "research",
    "workload": "workload",
    "followup": "followup",
    "report": "report",
}


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


def queue_l2(
    session: Session, *, run: AgentRun, proposed: Sequence[ProposedAction]
) -> list[dict[str, Any]]:
    """Queue the L2 proposals of ``run``; return a record for each refused one."""
    refused: list[dict[str, Any]] = []
    for proposal in proposed:
        if proposal.level != "L2":
            continue
        if not arguments_ok(proposal.arguments):
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
        if run.meeting_id is not None:
            session.execute(
                update(AgentPendingAction)
                .where(
                    AgentPendingAction.team_id == run.team_id,
                    AgentPendingAction.meeting_id == run.meeting_id,
                    AgentPendingAction.subagent == (run.route or ""),
                    AgentPendingAction.status == "pending",
                    AgentPendingAction.run_id
                    != run.id,  # one run's proposals never supersede each other
                )
                .values(status="superseded")
            )
        session.add(
            AgentPendingAction(
                team_id=run.team_id,
                meeting_id=run.meeting_id,
                run_id=run.id,
                subagent=run.route or "",
                tool=proposal.tool,
                kind=proposal.kind,
                arguments=dict(proposal.arguments),
                evidence=list(proposal.evidence),
                scope=scope_for(run.route),
            )
        )
        session.flush()
    return refused
