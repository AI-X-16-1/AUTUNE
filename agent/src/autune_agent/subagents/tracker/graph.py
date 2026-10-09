"""The Tracker subgraph: read what has stopped moving, propose.

Two nodes, no model call, no checkpointer (agent/CLAUDE.md rule 6). It reads
through its ``Toolbox`` only and calls no write: each change leaves as a
``ProposedAction`` for the main agent to put through plan mode, where a person
holding ``any`` -- the manager -- accepts or refuses each one. ``tracker`` is
not in ``pending.SCOPES`` on purpose: it asks for no approval scope of its own
(mkkim68 on #856).

``team_id`` is never passed here. It is the run's scope (B's ``RUN_SCOPE``),
bound by the main agent from the authenticated caller, the processed meeting's
team, or the periodic tick's team.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from autune_agent.main.registry import Toolbox
from autune_agent.main.subagents import CompiledSubagent, SubagentState
from autune_agent.results import Finding, ProposedAction, SubagentResult, ToolResult

from . import plan

STALLED = "extraction.stalled_action_items"
SET_DUE_DATE = "extraction.set_action_item_due_date"
"""The write a proposal names. B's ``ACTIONS``; the main agent runs it only
after approval."""

TOOLS = (STALLED,)

KST = ZoneInfo("Asia/Seoul")
"""The team's calendar day, as a due date is read."""


class TrackerState(SubagentState, total=False):
    stalled: ToolResult


def _today() -> date:
    return datetime.now(KST).date()


def build(toolbox: Toolbox) -> CompiledSubagent:
    def read(state: TrackerState) -> dict[str, Any]:
        stalled = toolbox.call(STALLED)
        if not stalled.ok:
            failed = ToolResult.failure(
                stalled.reason or "stalled items unreadable",
                "멈춰 있는 액션아이템을 읽지 못했습니다.",
            )
            return {"outcome": SubagentResult(result=failed)}
        return {"stalled": stalled}

    def propose(state: TrackerState) -> dict[str, Any]:
        items = plan.stalled_from(state["stalled"])
        moves = plan.plan_moves(items, today=_today())
        if moves:
            # Late items this run made no card for: the ones the tool counted
            # past its five rows, and any it showed past ``MAX_PROPOSALS``.
            late = sum(item.overdue for item in items)
            more = late - len(moves) + plan.late_not_shown(state["stalled"])
            rest = f"(가장 오래 지난 순서, {more}건 더 남음)" if more else ""
            summary = (
                f"기한이 지난 확정 항목에 기한 옮기기 제안 {len(moves)}건{rest}. "
                "관리자가 하나씩 승인해야 실행됩니다."
            )
        else:
            summary = "기한이 지난 확정 항목이 없어 제안하지 않습니다."
        waiting = plan.carried_only(items)
        if waiting:
            summary += f" 기한이 지나지 않은 {plan.CARRIED_WORDS} {waiting}건은 제안하지 않습니다."
        result = ToolResult(
            ok=True,
            summary=summary,
            items=[
                Finding.model_validate(
                    {
                        "title": m.title,
                        "body": m.rationale,
                        "score": 0.9 if m.item.carried else 0.7,
                        "id": m.item.action_item_id,
                    }
                )
                for m in moves
            ],
            evidence=[m.item.action_item_id for m in moves],
            truncated=state["stalled"].truncated,
        )
        proposed = [
            ProposedAction(
                kind=plan.MOVE,
                title=m.title,
                body=m.rationale,
                tool=SET_DUE_DATE,
                arguments={
                    "action_item_id": m.item.action_item_id,
                    "due_date": m.due_date.isoformat(),
                    # The item's meeting, so the waiting row and its card
                    # are that meeting's (#959).
                    **({"meeting_id": m.item.meeting_id} if m.item.meeting_id else {}),
                },
                level="L2",
                rationale=m.rationale,
                evidence=[m.item.action_item_id],
            )
            for m in moves
        ]
        return {"outcome": SubagentResult(result=result, proposed=proposed)}

    def after_read(state: TrackerState) -> str:
        return END if "outcome" in state else "propose"

    graph = StateGraph(TrackerState)
    graph.add_node("read", read)
    graph.add_node("propose", propose)
    graph.add_edge(START, "read")
    graph.add_conditional_edges("read", after_read, ["propose", END])
    graph.add_edge("propose", END)
    # The subgraph's state adds a key to ``SubagentState``; the main agent
    # writes ``request`` and reads ``outcome``, which is all the protocol promises.
    return cast(CompiledSubagent, graph.compile())
