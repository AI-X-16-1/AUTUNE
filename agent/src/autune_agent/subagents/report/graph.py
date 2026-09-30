"""The Report subagent's graph: find the meeting, read four tools, render, propose.

Fixed tool order, no LLM. Reads through the Toolbox only and never writes: the
outcome carries one L1 ProposedAction naming E's action, which the main agent
runs (agent-layer.md section 8 rule 2).
"""

from __future__ import annotations

import logging
import re

from langgraph.graph import END, START, StateGraph

from autune_agent.main import SubagentState, Toolbox
from autune_agent.main.subagents import CompiledSubagent
from autune_agent.results import ProposedAction, SubagentResult, ToolResult

from .render import has_pending, render

log = logging.getLogger(__name__)

ACTIONS_TOOL = "extraction.meeting_action_items"
REVIEW_TOOL = "extraction.review_state"
GAPS_TOOL = "gap.meeting_gaps"
"""Placeholder until C's tools.py ships; match it to C's real name then. A tool
that is not registered is skipped, so the gap section is just absent."""
LINKS_TOOL = "context.links_for_meeting"
PUBLISH_TOOL = "intelligence.publish_meeting_report"
TOOLS = (ACTIONS_TOOL, REVIEW_TOOL, GAPS_TOOL, LINKS_TOOL)
OPTIONAL = (GAPS_TOOL, LINKS_TOOL)
"""Context around B's confirmed items. A failure here drops a section, never the report."""

# ASCII boundaries, not \b: in a str pattern \b is Unicode-aware, so a Korean
# particle right after the id ("mtg_ab12cd의") would count as part of the word.
_MEETING_ID = re.compile(r"(?<![A-Za-z0-9_])mtg_[A-Za-z0-9]+(?![A-Za-z0-9_])")


def _meeting_id(request: str) -> str | None:
    """The one meeting the request names; None for none or for several."""
    found = set(_MEETING_ID.findall(request))
    return found.pop() if len(found) == 1 else None


def _read(toolbox: Toolbox, name: str, meeting_id: str) -> ToolResult | None:
    if name not in toolbox.describe():
        return None  # not shipped yet: no call, no budget spent
    if name not in OPTIONAL:
        return toolbox.call(name, meeting_id=meeting_id)
    try:
        return toolbox.call(name, meeting_id=meeting_id)
    except Exception as exc:  # a bug in C's or D's tool costs its section only
        log.warning("report_optional_tool_failed tool=%s error=%s", name, type(exc).__name__)
        return None


def build(toolbox: Toolbox) -> CompiledSubagent:
    def report(state: SubagentState) -> SubagentState:
        meeting_id = _meeting_id(state.get("request", ""))
        if meeting_id is None:
            failure = ToolResult.failure("the request names no single meeting id")
            return {"outcome": SubagentResult(result=failure)}
        results = {name: _read(toolbox, name, meeting_id) for name in TOOLS}
        actions = results[ACTIONS_TOOL]
        if actions is not None and not actions.ok:
            # B answers ok=False only for a meeting it cannot find: nothing to report.
            return {"outcome": SubagentResult(result=actions)}
        body = render(actions, results[REVIEW_TOOL], results[GAPS_TOOL], results[LINKS_TOOL])
        if not body:
            failure = ToolResult.failure("nothing to report for this meeting")
            return {"outcome": SubagentResult(result=failure)}
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
