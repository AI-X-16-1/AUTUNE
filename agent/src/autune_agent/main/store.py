"""Every run leaves an ``agent_runs`` row (agent-layer.md section 5).

"Why did it do that" is the only question anyone asks about an agent, so the row
is written whatever happened -- answered, unrouted, stopped by the budget, or
broken by a bug. The budget case keeps the trace up to the call that stopped it
(section 9), which a bare exception out of ``graph.invoke`` would lose.

**What the row holds, and what it does not.** ``steps`` are tool names and
evidence ids. The answer text is stored only when the run is about a meeting,
because then ``meeting_id`` cascades it away with the meeting (privacy.md
section 7). A chat run about no meeting may still quote one in its answer, and
that copy would outlive the meeting it quoted -- so it is returned to the person
who asked and not kept, and a proposed action keeps its kind, tool, level and
evidence ids but not its title or body.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from autune_agent.models import AgentRun

from .graph import MainState, run
from .registry import BudgetExceededError, CallBudget, Tool
from .router import Router
from .subagents import Subagent

BUDGET_ANSWER = "한 번에 확인할 수 있는 범위를 넘었습니다. 질문을 좁혀서 다시 물어봐 주세요."


def run_and_record(
    request: str,
    *,
    session: Session,
    router: Router,
    team_id: str,
    trigger: Mapping[str, Any],
    requested_by: str | None = None,
    meeting_id: str | None = None,
    subagents: Mapping[str, Subagent] | None = None,
    tools: Mapping[str, Tool] | None = None,
    budget: CallBudget | None = None,
) -> tuple[AgentRun, MainState]:
    budget = budget or CallBudget()
    started = time.monotonic()
    state: MainState = {"request": request}
    row = AgentRun(
        team_id=team_id,
        meeting_id=meeting_id,
        requested_by=requested_by,
        trigger=dict(trigger),
    )
    try:
        state = run(
            request,
            session=session,
            router=router,
            subagents=subagents,
            tools=tools,
            budget=budget,
        )
        row.outcome = "answered" if state.get("route") else "unrouted"
    except BudgetExceededError:
        row.outcome = "budget_exceeded"
        state["answer"] = BUDGET_ANSWER
    except Exception:
        row.outcome = "failed"
        _finish(row, state, budget, started, meeting_id)
        session.add(row)
        session.commit()
        raise
    _finish(row, state, budget, started, meeting_id)
    session.add(row)
    session.commit()
    return row, state


def _finish(
    row: AgentRun, state: MainState, budget: CallBudget, started: float, meeting_id: str | None
) -> None:
    row.route = state.get("route")
    row.steps = list(budget.steps)
    outcome = state.get("outcome")
    proposed = outcome.proposed if outcome else []
    if meeting_id:
        row.proposed = [a.model_dump(mode="json") for a in proposed]
        row.answer = state.get("answer")
    else:
        # Same reason as the answer: a title or body may quote a meeting.
        row.proposed = [
            {"kind": a.kind, "tool": a.tool, "level": a.level, "evidence": a.evidence}
            for a in proposed
        ]
        row.answer = None
    row.latency_ms = int((time.monotonic() - started) * 1000)
