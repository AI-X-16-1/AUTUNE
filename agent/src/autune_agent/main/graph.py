"""The main agent as a LangGraph supervisor (agent-layer.md section 3.3).

    START -> route -> delegate | unrouted -> answer -> END

``delegate`` invokes one subagent's compiled subgraph with the run's shared
tool budget. Proposed actions come back in the outcome and are **not executed**
here: plan mode and the approval screen are the next milestone, and until they
exist nothing at L1 or L2 happens at all.

Compiled without a checkpointer. Section 3.3: LangGraph's own tables would hold
tool results with no deletion path by meeting, so run state goes to
``agent_runs`` instead once that table exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from autune_agent.results import SubagentResult, ToolResult

from .registry import CallBudget, Tool, Toolbox, collect_tools
from .router import Router
from .subagents import CompiledSubagent, Subagent, collect_subagents


class MainState(TypedDict, total=False):
    request: str
    route: str | None
    outcome: SubagentResult
    answer: str


def build_main_graph(
    *,
    session: Session,
    router: Router,
    subagents: Mapping[str, Subagent],
    tools: Mapping[str, Tool],
    budget: CallBudget,
) -> Any:
    compiled: dict[str, CompiledSubagent] = {
        name: sub.build(Toolbox(tools, session, budget, allowed=sub.tools))
        for name, sub in subagents.items()
    }
    options = {name: sub.description for name, sub in subagents.items()}

    def route(state: MainState) -> MainState:
        name = router.route(state["request"], options)
        return {"route": name if name in compiled else None}

    def delegate(state: MainState) -> MainState:
        name = state["route"]
        assert name is not None  # the edge below only comes here with a route
        out = compiled[name].invoke({"request": state["request"]})
        return {"outcome": SubagentResult.model_validate(out["outcome"])}

    def unrouted(state: MainState) -> MainState:
        result = ToolResult.failure("no subagent fits this request")
        return {"outcome": SubagentResult(result=result)}

    def answer(state: MainState) -> MainState:
        return {"answer": router.compose(state["request"], state["outcome"])}

    graph = StateGraph(MainState)
    graph.add_node("route", route)
    graph.add_node("delegate", delegate)
    graph.add_node("unrouted", unrouted)
    graph.add_node("answer", answer)
    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route", lambda s: "delegate" if s.get("route") else "unrouted", ["delegate", "unrouted"]
    )
    graph.add_edge("delegate", "answer")
    graph.add_edge("unrouted", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


def run(
    request: str,
    *,
    session: Session,
    router: Router,
    subagents: Mapping[str, Subagent] | None = None,
    tools: Mapping[str, Tool] | None = None,
    budget: CallBudget | None = None,
) -> MainState:
    """One chat turn or one trigger, start to finish."""
    graph = build_main_graph(
        session=session,
        router=router,
        subagents=collect_subagents() if subagents is None else subagents,
        tools=collect_tools() if tools is None else tools,
        budget=budget or CallBudget(),
    )
    state: MainState = graph.invoke({"request": request})
    return state
