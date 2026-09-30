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
mid-run gets the event again. A subagent that already has a finished run for
this event and meeting is skipped, which is what keeps an L1 write from
happening twice. A ``failed`` run does not count as finished, so a redelivery
retries it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent.config import get_agent_settings
from autune_agent.models import AgentRun
from autune_agent.results import SubagentResult
from autune_core import Meeting
from autune_core.errors import PrivacyViolationError

from .actions import Action
from .registry import Tool
from .store import run_and_record
from .subagents import TRIGGER_EVENTS, Subagent, collect_subagents

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
        if _already_ran(session, event=event, meeting_id=meeting_id, route=sub.name):
            log.info("agent_trigger_redelivered event=%s subagent=%s", event, sub.name)
            continue
        try:
            row, _ = run_and_record(
                event,
                session=session,
                router=SummaryRouter(),
                team_id=meeting.team_id,
                meeting_id=meeting_id,
                trigger={"kind": "event", "event": event},
                subagents=everyone,
                tools=tools,
                actions=actions,
                route_to=sub.name,
            )
        except PrivacyViolationError:
            log.error("agent_trigger_privacy_violation subagent=%s", sub.name)
            session.rollback()
            violations.append(sub.name)
            continue
        except Exception as exc:  # noqa: BLE001 - one subagent's bug must not stop the others
            # The failed row is already written by run_and_record; logged by
            # type only, the message may carry meeting text.
            log.error("agent_trigger_failed subagent=%s error=%s", sub.name, type(exc).__name__)
            session.rollback()
            continue
        rows.append(row)
    if violations:
        raise PrivacyViolationError(
            f"unmasked value in {len(violations)} subagent run(s): {', '.join(violations)}"
        )
    return rows


def _already_ran(session: Session, *, event: str, meeting_id: str, route: str) -> bool:
    earlier = session.scalars(
        select(AgentRun.trigger).where(
            AgentRun.meeting_id == meeting_id,
            AgentRun.route == route,
            AgentRun.outcome != "failed",
        )
    )
    return any(t.get("kind") == "event" and t.get("event") == event for t in earlier)
