"""The Report subagent's graph: find the meeting, read four tools, render, propose.

Fixed tool order, no LLM. Reads through the Toolbox only and never writes: the
outcome carries one L1 ProposedAction naming E's action, which the main agent
runs (agent-layer.md section 8 rule 2).
"""

from __future__ import annotations

import re

from langgraph.graph import END, START, StateGraph

from autune_agent.main import SubagentState, Toolbox
from autune_agent.main.subagents import CompiledSubagent
from autune_agent.results import ProposedAction, SubagentResult, ToolResult

from .render import has_pending, render

ACTIONS_TOOL = "extraction.meeting_action_items"
REVIEW_TOOL = "extraction.review_state"
GAPS_TOOL = "gap.meeting_gaps"
"""Placeholder until C's tools.py ships; match it to C's real name then. A tool
that is not registered is refused by the Toolbox, so the gap section is absent."""
LINKS_TOOL = "context.links_for_meeting"
PUBLISH_TOOL = "intelligence.publish_meeting_report"
TOOLS = (ACTIONS_TOOL, REVIEW_TOOL, GAPS_TOOL, LINKS_TOOL)

_MEETING_ID = re.compile(r"\bmtg_[A-Za-z0-9]+\b")


def build(toolbox: Toolbox) -> CompiledSubagent:
    def report(state: SubagentState) -> SubagentState:
        found = _MEETING_ID.search(state.get("request", ""))
        if found is None:
            failure = ToolResult.failure("no meeting id in the request")
            return {"outcome": SubagentResult(result=failure)}
        meeting_id = found.group()
        results = {name: toolbox.call(name, meeting_id=meeting_id) for name in TOOLS}
        body = render(
            results[ACTIONS_TOOL], results[REVIEW_TOOL], results[GAPS_TOOL], results[LINKS_TOOL]
        )
        proposal = ProposedAction(
            kind="meeting_report",
            title="회의 리포트 게시",
            tool=PUBLISH_TOOL,
            # No team_id: the run's authenticated scope fills it (E's RUN_SCOPE).
            arguments={
                "meeting_id": meeting_id,
                "body_markdown": body,
                "pending_review": has_pending(results[REVIEW_TOOL]),
            },
            level="L1",
            rationale="The meeting's analysis finished; post its structured minutes.",
        )
        summary = ToolResult(ok=True, summary="회의 리포트를 만들었습니다.", items=[])
        return {"outcome": SubagentResult(result=summary, proposed=[proposal])}

    # One node: the four steps are a straight line with nothing to branch on.
    # Split when an LLM summary step (option C of the design) is added.
    graph = StateGraph(SubagentState)
    graph.add_node("report", report)
    graph.add_edge(START, "report")
    graph.add_edge("report", END)
    return graph.compile()
