"""Every run leaves an ``agent_runs`` row (agent-layer.md section 5).

"Why did it do that" is the only question anyone asks about an agent, so the row
is written whatever happened -- answered, unrouted, stopped by the budget, or
broken by a bug. The budget case keeps the trace up to the call that stopped it
(section 9), which a bare exception out of ``graph.invoke`` would lose.

**What the row holds, and what it does not.** ``steps`` are tool names and
evidence ids; a proposed action keeps its kind, tool, level and evidence ids;
``actions`` keeps what ran. **No run keeps its answer, or a proposal's title,
body or arguments** -- a run about a meeting included.

An earlier version kept them for a run with a ``meeting_id``, on the reasoning
that the cascade from ``meetings`` would delete them with the meeting (privacy.md
section 7). That holds only if every sentence in the answer came from *that*
meeting, and it does not: B's ``open_action_items`` reads every meeting of the
team, and D's links and decision threads exist to return past meetings. A run
about meeting M quoting meeting M1 would keep M1's words after M1 was deleted
(#449 review). Keeping text only when every evidence id belongs to M was the
other option, and it cannot be checked here: an ``act_`` or ``dec_`` id's
meeting is known only to the module that owns it. The answer still goes back
to whoever asked; it is just not stored. Plan mode, which must keep a proposal
until a person approves it, has to answer this again for ``messages``.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from autune_agent.models import AgentRun

from .actions import Action, ActionPrivacyViolationError, collect_actions, execute_l1
from .graph import MainState, run
from .notify import tell_approvers
from .own_tools import collect_own_actions
from .pending import queue_l2
from .registry import BudgetExceededError, CallBudget, RunScope, Tool
from .router import Router
from .subagents import Subagent, collect_subagents

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
    actions: Mapping[str, Action] | None = None,
    route_to: str | None = None,
    notify: bool = True,
) -> tuple[AgentRun, MainState]:
    """Run, carry out what the run proposed at L1, and record both.

    ``route_to`` skips the router, for a trigger that already knows which
    subagent it woke. L1 runs after the graph and before the row is written,
    so the row says what was done; L2 stays proposed (``main/actions.py``).
    ``notify=False`` leaves telling the approvers to a caller that records
    several runs and tells once (``main/notify.py``).
    """
    budget = budget or CallBudget()
    # Collected here rather than inside the graph, so the queue below reads the
    # same declarations the run was routed among (chat passes none).
    subagents = collect_subagents() if subagents is None else subagents
    scope = RunScope(team_id=team_id, meeting_id=meeting_id)
    started = time.monotonic()
    state: MainState = {"request": request}
    # One mapping for both halves: execute_l1 keeps an L2-declared action for
    # approval, and queue_l2 must see the same levels to queue it.
    declared: Mapping[str, Action] = {} if actions is None else actions
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
            scope=scope,
            subagents=subagents,
            tools=tools,
            budget=budget,
            route_to=route_to,
        )
        row.outcome = "answered" if state.get("route") else "unrouted"
        outcome = state.get("outcome")
        if outcome is not None and outcome.proposed:
            if actions is None:
                declared = {**collect_actions(), **collect_own_actions()}
            try:
                row.actions = execute_l1(
                    outcome.proposed,
                    actions=declared,
                    session=session,
                    scope=scope,
                )
            except ActionPrivacyViolationError as exc:
                # The other actions ran; the row says so, then the run fails.
                row.actions = exc.done
                raise
    except BudgetExceededError:
        row.outcome = "budget_exceeded"
        state["answer"] = BUDGET_ANSWER
    except Exception:
        row.outcome = "failed"
        # When the graph itself raised it returned no state, so without this a
        # triggered run that crashed would not say which subagent it was for.
        # A graph that returned and then failed in its actions keeps its route.
        state.setdefault("route", route_to)
        _finish(row, state, budget, started, meeting_id)
        session.add(row)
        session.commit()
        raise
    _finish(row, state, budget, started, meeting_id)
    session.add(row)
    if row.outcome == "answered":
        session.flush()  # row.id for the queue
        outcome = state.get("outcome")
        if outcome is not None and outcome.proposed:
            woken = subagents.get(row.route or "")
            row.actions = [
                *row.actions,
                *queue_l2(
                    session,
                    run=row,
                    proposed=outcome.proposed,
                    actions=declared,
                    team_wide=woken is not None and woken.proposals_per == "team",
                ),
            ]
    session.commit()
    if notify and row.outcome == "answered":
        tell_approvers(session, team_id=team_id, run_ids=[row.id], asked_by=requested_by)
    return row, state


def _finish(
    row: AgentRun, state: MainState, budget: CallBudget, started: float, meeting_id: str | None
) -> None:
    row.route = state.get("route")
    row.steps = list(budget.steps)
    outcome = state.get("outcome")
    proposed = outcome.proposed if outcome else []
    # A title, body or argument may quote any meeting; see the module docstring.
    row.proposed = [
        {"kind": a.kind, "tool": a.tool, "level": a.level, "evidence": a.evidence} for a in proposed
    ]
    row.answer = None
    row.latency_ms = int((time.monotonic() - started) * 1000)
