"""The Report subagent's graph: a trigger runs the template, a chat runs the E agent.

``template.py`` is the fixed path (no LLM) a pipeline event wakes; ``chat.py`` is
the E agent's tool loop for a person's question (spec
agent/docs/specs/2026-10-05-e-agent-design.md).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from autune_agent.main import SubagentState, Toolbox
from autune_agent.main.subagents import CompiledSubagent
from autune_agent.results import SubagentResult, ToolResult

from . import chat
from .template import *  # noqa: F403 - the names tests and __init__ import from here
from .template import (
    CHANGED_TRIGGER,
    MEETING_ID,
    POSTED,
    TRIGGERS,
    already_posted,
    compose_report,
    failed,
    repropose,
)


def template_run(toolbox: Toolbox, request: str) -> SubagentState:
    """Today's behaviour for any request: the template path."""
    if request == CHANGED_TRIGGER:
        return repropose(toolbox)
    named = set(MEETING_ID.findall(request))
    if len(named) > 1:
        return failed("the request names several meetings")
    # None named: the run's scope carries the meeting (or the Toolbox refuses).
    meeting: dict[str, Any] = {"meeting_id": named.pop()} if named else {}
    if already_posted(toolbox, meeting):
        # Asked in chat too, so say where a fix goes (#658 review).
        return {"outcome": SubagentResult(result=ToolResult(ok=True, summary=POSTED, items=[]))}
    return {"outcome": compose_report(toolbox, meeting)}


def build(toolbox: Toolbox) -> CompiledSubagent:
    def report(state: SubagentState) -> SubagentState:
        request = state.get("request", "")
        if request in TRIGGERS:
            return template_run(toolbox, request)
        model = chat.MODEL_FACTORY()
        if model is None:
            return template_run(toolbox, request)
        return chat.chat_run(toolbox, request, model)

    # One node: the steps are a straight line with nothing to branch on. Split
    # when an LLM summary step (option C of the design) is added.
    graph = StateGraph(SubagentState)
    graph.add_node("report", report)
    graph.add_edge(START, "report")
    graph.add_edge("report", END)
    return graph.compile()
