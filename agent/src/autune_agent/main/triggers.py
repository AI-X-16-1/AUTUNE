"""An event wakes the subagents that asked for it (agent-layer.md section 6).

**Every trigger enters through the main agent.** A subagent lists the events it
wants in ``Subagent.triggers``; the main agent holds the one subscription per
event (``autune_agent.tasks``) and starts one run per subagent that asked, each
an ordinary ``agent_runs`` row with the same shape a chat turn leaves.

**A woken run calls no model of ours.** The subagent is named, so there is
nothing to route, and nobody is waiting for a chat answer, so the answer is the
subagent's own summary (``SummaryRouter``). What the subagent calls inside its
own graph is its business and goes through the same guard as anything else.

**A privacy violation fails the task.** Any other exception in one subagent's
run is logged and the others still run -- its ``failed`` row lets a redelivery
retry it. A ``PrivacyViolationError`` is never downgraded that way: the other
subagents still run, then the task raises with their names (#509 review, the
same shape as #506), so the worker shows a failed task rather than a log line.

**Subagents that read B, C or D wake on ``intelligence.completed``.**
``transcript.ready`` reaches B, C and D at the same moment it reaches this
layer, so their results do not exist yet; only ``intelligence.completed``
arrives after all three have finished.

**Safe to deliver twice.** Tasks are ``acks_late``, so a worker that dies
mid-run gets the event again. The redelivery guard keys on the Celery task id:
a redelivered task carries the same id and is skipped; when E re-publishes the
event (plan-mode spec §3), it is a new task with a new id and runs again. A
subagent with a failed run for the same task id is retried. A ``failed`` run
with a different task id does not block the same subagent in a fresh task.

**A timer wakes the subagents that declared a period** (``Periodic``, #634).
One beat task (``autune.agent.periodic.wake_subagents``) ticks every
``PERIODIC_TICK`` and calls ``on_tick``, which starts, for each team that has
members, a run of each subagent whose period has elapsed since its last
periodic run for that team. "Elapsed" allows half a tick of slack, so a
six-hour period fires every six ticks rather than drifting to seven. A
redelivered or overlapping tick finds the run the first one left and skips;
a failed run does not count, so the next tick retries it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent.config import get_agent_settings
from autune_agent.models import AgentRun
from autune_agent.results import SubagentResult
from autune_core import Meeting, TeamMember
from autune_core.errors import PrivacyViolationError

from .actions import Action
from .notify import tell_approvers
from .registry import Tool
from .store import run_and_record
from .subagents import (
    PERIODIC_REQUEST,
    PERIODIC_TICK,
    TRIGGER_EVENTS,
    Subagent,
    collect_subagents,
)

log = logging.getLogger(__name__)


class SummaryRouter:
    """The router for a run nobody is chatting in. It never routes and never
    calls a model: the answer is what the subagent summarised."""

    def route(self, request: str, subagents: Mapping[str, str]) -> str | None:
        return None

    def compose(self, request: str, outcome: SubagentResult) -> str:
        return outcome.result.summary


def on_event(
    event: str,
    meeting_id: str,
    *,
    session: Session,
    subagents: Mapping[str, Subagent] | None = None,
    tools: Mapping[str, Tool] | None = None,
    actions: Mapping[str, Action] | None = None,
    task_id: str | None = None,
) -> list[AgentRun]:
    """Start a run for each subagent that asked for ``event``. Returns the new rows."""
    if event not in TRIGGER_EVENTS:
        raise ValueError(f"{event!r} is not an event the agent layer listens to")
    if get_agent_settings().router_impl == "off":
        # The layer is off; the fixed pipeline carries on without it.
        return []
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        # Deleted between the event and now. Nothing to run on.
        log.info("agent_trigger_no_meeting event=%s meeting_id=%s", event, meeting_id)
        return []
    everyone = collect_subagents() if subagents is None else subagents
    woken = [s for s in everyone.values() if event in s.triggers]
    rows: list[AgentRun] = []
    violations: list[str] = []
    for sub in woken:
        if _already_ran(
            session, event=event, meeting_id=meeting_id, route=sub.name, task_id=task_id
        ):
            log.info("agent_trigger_redelivered event=%s subagent=%s", event, sub.name)
            continue
        row = _run_woken(
            session,
            sub,
            request=event,
            team_id=meeting.team_id,
            meeting_id=meeting_id,
            trigger={"kind": "event", "event": event, **({"task_id": task_id} if task_id else {})},
            everyone=everyone,
            tools=tools,
            actions=actions,
            violations=violations,
        )
        if row is not None:
            rows.append(row)
    # Once for the whole event, not per subagent (main/notify.py).
    tell_approvers(session, team_id=meeting.team_id, run_ids=[r.id for r in rows])
    _raise_for(violations)
    return rows


def on_tick(
    *,
    session: Session,
    now: datetime | None = None,
    subagents: Mapping[str, Subagent] | None = None,
    tools: Mapping[str, Tool] | None = None,
    actions: Mapping[str, Action] | None = None,
    task_id: str | None = None,
) -> list[AgentRun]:
    """Start a run of each due periodic subagent for each team with members."""
    if get_agent_settings().router_impl == "off":
        return []
    everyone = collect_subagents() if subagents is None else subagents
    periodic = [s for s in everyone.values() if s.period is not None]
    if not periodic:
        return []
    now = now or datetime.now(UTC)
    teams = session.scalars(select(TeamMember.team_id).distinct().order_by(TeamMember.team_id))
    rows: list[AgentRun] = []
    violations: list[str] = []
    for team_id in list(teams):
        for sub in periodic:
            period = sub.period
            assert period is not None  # filtered above
            since = now - period.every + PERIODIC_TICK / 2
            if _ran_periodically(session, team_id=team_id, route=sub.name, since=since):
                continue
            row = _run_woken(
                session,
                sub,
                request=PERIODIC_REQUEST,
                team_id=team_id,
                meeting_id=None,
                trigger={"kind": "periodic", **({"task_id": task_id} if task_id else {})},
                everyone=everyone,
                tools=tools,
                actions=actions,
                violations=violations,
            )
            if row is not None:
                rows.append(row)
    for team_id in sorted({r.team_id for r in rows}):
        tell_approvers(
            session, team_id=team_id, run_ids=[r.id for r in rows if r.team_id == team_id]
        )
    _raise_for(violations)
    return rows


def _run_woken(
    session: Session,
    sub: Subagent,
    *,
    request: str,
    team_id: str,
    meeting_id: str | None,
    trigger: Mapping[str, Any],
    everyone: Mapping[str, Subagent],
    tools: Mapping[str, Tool] | None,
    actions: Mapping[str, Action] | None,
    violations: list[str],
) -> AgentRun | None:
    """One subagent's run; ``None`` when it failed and the others should go on."""
    try:
        row, _ = run_and_record(
            request,
            session=session,
            router=SummaryRouter(),
            team_id=team_id,
            meeting_id=meeting_id,
            trigger=trigger,
            subagents=everyone,
            tools=tools,
            actions=actions,
            route_to=sub.name,
            notify=False,
        )
    except PrivacyViolationError:
        log.error("agent_trigger_privacy_violation subagent=%s", sub.name)
        session.rollback()
        violations.append(sub.name)
        return None
    except Exception as exc:  # noqa: BLE001 - one subagent's bug must not stop the others
        # The failed row is already written by run_and_record; logged by
        # type only, the message may carry meeting text.
        log.error("agent_trigger_failed subagent=%s error=%s", sub.name, type(exc).__name__)
        session.rollback()
        return None
    return row


def _raise_for(violations: list[str]) -> None:
    if violations:
        raise PrivacyViolationError(
            f"unmasked value in {len(violations)} subagent run(s): {', '.join(violations)}"
        )


def _ran_periodically(session: Session, *, team_id: str, route: str, since: datetime) -> bool:
    """A periodic run of ``route`` for the team since ``since`` that did not fail."""
    recent = session.scalars(
        select(AgentRun.trigger).where(
            AgentRun.team_id == team_id,
            AgentRun.meeting_id.is_(None),
            AgentRun.route == route,
            AgentRun.outcome != "failed",
            AgentRun.created_at >= since,
        )
    )
    return any(t.get("kind") == "periodic" for t in recent)


def _already_ran(
    session: Session, *, event: str, meeting_id: str, route: str, task_id: str | None
) -> bool:
    earlier = session.scalars(
        select(AgentRun.trigger).where(
            AgentRun.meeting_id == meeting_id,
            AgentRun.route == route,
            AgentRun.outcome != "failed",
        )
    )
    if task_id is not None:
        # A redelivery carries the same task id; E's re-publish is a new task (plan-mode spec §3).
        return any(t.get("task_id") == task_id for t in earlier)
    return any(t.get("kind") == "event" and t.get("event") == event for t in earlier)
