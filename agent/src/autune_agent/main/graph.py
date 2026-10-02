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

from .ask import ASK_ROUTE, ask, declare, tool_set
from .own_tools import collect_own_tools
from .registry import CallBudget, RunScope, Tool, Toolbox, collect_tools, refuse_tracing
from .router import Router
from .subagents import CompiledSubagent, Subagent, collect_subagents
from .toolcall import ToolModel


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
    scope: RunScope,
    route_to: str | None = None,
    asker: ToolModel | None = None,
) -> Any:
    refuse_tracing()
    compiled: dict[str, CompiledSubagent] = {
        name: sub.build(Toolbox(tools, session, budget, allowed=sub.tools, scope=scope))
        for name, sub in subagents.items()
    }
    options = {name: sub.description for name, sub in subagents.items()}

    def route(state: MainState) -> MainState:
        # A trigger names its subagent; only a chat message is routed by a model.
        name = route_to if route_to is not None else router.route(state["request"], options)
        return {"route": name if name in compiled else None}

    def delegate(state: MainState) -> MainState:
        name = state["route"]
        assert name is not None  # the edge below only comes here with a route
        out = compiled[name].invoke({"request": state["request"]})
        return {"outcome": SubagentResult.model_validate(out["outcome"])}

    def unrouted(state: MainState) -> MainState:
        result = ToolResult.failure("no subagent fits this request")
        return {"outcome": SubagentResult(result=result)}

    def ask_node(state: MainState) -> MainState:
        assert asker is not None
        box = Toolbox(tools, session, budget, allowed=tool_set(scope), scope=scope)
        result = ask(state["request"], model=asker, toolbox=box, declarations=declare(tools, scope))
        return {"route": ASK_ROUTE, "outcome": SubagentResult(result=result)}

    def answer(state: MainState) -> MainState:
        return {"answer": router.compose(state["request"], state["outcome"])}

    asks = asker is not None and route_to is None
    graph = StateGraph(MainState)
    graph.add_node("route", route)
    graph.add_node("delegate", delegate)
    graph.add_node("unrouted", ask_node if asks else unrouted)
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
    scope: RunScope,
    subagents: Mapping[str, Subagent] | None = None,
    tools: Mapping[str, Tool] | None = None,
    budget: CallBudget | None = None,
    route_to: str | None = None,
    asker: ToolModel | None = None,
) -> MainState:
    """One chat turn or one trigger, start to finish.

    ``scope`` has no default for the reason ``Toolbox.allowed`` has none: a
    caller that forgets it must fail, not run unscoped.
    """
    graph = build_main_graph(
        session=session,
        router=router,
        subagents=collect_subagents() if subagents is None else subagents,
        tools={**collect_tools(), **collect_own_tools()} if tools is None else tools,
        budget=budget or CallBudget(),
        scope=scope,
        route_to=route_to,
        asker=asker,
    )
    state: MainState = graph.invoke({"request": request})
    return state
