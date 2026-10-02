"""The Workload subgraph: read the load, read the loaded people's items, propose.

Three nodes, no model call, no checkpointer (agent/CLAUDE.md rule 6). It reads
through its ``Toolbox`` only and calls no write: each reassignment leaves as a
``ProposedAction`` for the main agent to put through plan mode, where the
approver with scope ``workload`` -- the manager -- accepts or refuses each one.

``team_id`` is never passed here. It is the run's scope (B's ``RUN_SCOPE``), and
the main agent binds it from the authenticated caller -- ``POST /api/agent/chat``
after its membership check, or ``on_event`` from the processed meeting's team; a
subagent that chose it could read another team's work (autune-fb's review of
#449). ``tests/test_workload_chat.py`` runs it on B's real tools from the chat
route, ``tests/test_workload_after_meeting.py`` from the event.
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from autune_agent.main.registry import Toolbox
from autune_agent.main.subagents import CompiledSubagent, SubagentState
from autune_agent.results import Finding, ProposedAction, SubagentResult, ToolResult

from . import plan

LOAD = "extraction.workload_by_owner"
ITEMS = "extraction.person_action_items"
REASSIGN = "extraction.reassign_action_item"
"""The write each proposal names. B's ``ACTIONS``; the main agent runs it only
after approval."""

TOOLS = (LOAD, ITEMS)


class WorkloadState(SubagentState, total=False):
    load: ToolResult
    people: list[plan.Person]
    items_of: dict[str, list[plan.Candidate]]


def _stop(reason: str, summary: str) -> dict[str, Any]:
    return {"outcome": SubagentResult(result=ToolResult.failure(reason, summary))}


def build(toolbox: Toolbox) -> CompiledSubagent:
    def read_load(state: WorkloadState) -> dict[str, Any]:
        load = toolbox.call(LOAD)
        if not load.ok:
            return _stop(load.reason or "workload unreadable", "팀의 업무량을 읽지 못했습니다.")
        return {"load": load, "people": plan.people_from(load)}

    def read_items(state: WorkloadState) -> dict[str, Any]:
        items_of: dict[str, list[plan.Candidate]] = {}
        # Only when someone could take work: otherwise the calls buy nothing.
        if plan.takers(state["people"]):
            for giver in plan.givers(state["people"]):
                result = toolbox.call(ITEMS, user_id=giver.user_id)
                if result.ok:
                    items_of[giver.user_id] = plan.candidates_from(result)
        return {"items_of": items_of}

    def propose(state: WorkloadState) -> dict[str, Any]:
        people = state["people"]
        moves = plan.plan_moves(people, state["items_of"])
        loaded = plan.givers(people)
        free = [p for p in people if p.state == "free"]
        summary = f"팀원 {len(people)}명 중 몰림 {len(loaded)}명, 여유 {len(free)}명."
        if not loaded:
            summary += " 몰린 사람이 없어 재배정을 제안하지 않습니다."
        elif not moves:
            summary += " 넘겨받을 수 있는 사람이 없어 재배정을 제안하지 않습니다."
        else:
            summary += f" 재배정 제안 {len(moves)}건, 관리자가 하나씩 승인해야 실행됩니다."
        result = ToolResult(
            ok=True,
            summary=summary,
            items=[
                Finding.model_validate(
                    {
                        "title": m.title,
                        "body": m.rationale,
                        "score": m.item.score,
                        "id": m.item.action_item_id,
                    }
                )
                for m in moves
            ],
            evidence=[m.item.action_item_id for m in moves],
            truncated=state["load"].truncated,
        )
        proposed = [
            ProposedAction(
                kind="reassign_action_item",
                title=m.title,
                body=m.rationale,
                tool=REASSIGN,
                arguments={"action_item_id": m.item.action_item_id, "assignee_id": m.taker.user_id},
                level="L2",
                rationale=m.rationale,
                evidence=[m.item.action_item_id],
            )
            for m in moves
        ]
        return {"outcome": SubagentResult(result=result, proposed=proposed)}

    def after_load(state: WorkloadState) -> str:
        return END if "outcome" in state else "read_items"

    graph = StateGraph(WorkloadState)
    graph.add_node("read_load", read_load)
    graph.add_node("read_items", read_items)
    graph.add_node("propose", propose)
    graph.add_edge(START, "read_load")
    graph.add_conditional_edges("read_load", after_load, ["read_items", END])
    graph.add_edge("read_items", "propose")
    graph.add_edge("propose", END)
    # The subgraph's state adds keys to ``SubagentState``; the main agent writes
    # ``request`` and reads ``outcome``, which is all the protocol promises.
    return cast(CompiledSubagent, graph.compile())
