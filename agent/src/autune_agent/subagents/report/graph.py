"""The Report subagent's graph: find the meeting, read four tools, render, propose.

Fixed tool order, no LLM. Reads through the Toolbox only and never writes: the
outcome carries two proposals naming E's actions -- store the draft (E lists it
in ``L1_ACTIONS``, so it runs at the end of the run) and post it (L2, after a
person approves). The main agent runs them (agent-layer.md section 8 rule 2).

Where the meeting comes from:

- **Woken by ``autune.intelligence.completed``** (#509): the request is the event
  name and the run's scope carries the meeting, so neither the tool calls nor
  the proposals name it -- the Toolbox and the executor fill it in.
- **Asked in chat:** the request must name exactly one ``mtg_...`` id. It is
  passed on, and the scope still holds it to the run's team.
"""

from __future__ import annotations

import logging
import re
from typing import Any

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
TOOLS = (ACTIONS_TOOL, REVIEW_TOOL, GAPS_TOOL, LINKS_TOOL)
OPTIONAL = (GAPS_TOOL, LINKS_TOOL)
"""Context around B's confirmed items. A failure here drops a section, never the report."""

DRAFT_ACTION = "intelligence.draft_meeting_report"
"""E stores the report. L1: E lists it in ``L1_ACTIONS``."""
PUBLISH_ACTION = "intelligence.publish_meeting_report"
"""E posts the stored report to the team channel. L2: a channel post moves people."""

TRIGGER = "autune.intelligence.completed"

# ASCII boundaries, not \b: in a str pattern \b is Unicode-aware, so a Korean
# particle right after the id ("mtg_ab12cd의") would count as part of the word.
_MEETING_ID = re.compile(r"(?<![A-Za-z0-9_])mtg_[A-Za-z0-9]+(?![A-Za-z0-9_])")


def _meeting_id(request: str) -> str | None:
    """The one meeting the request names; None for none or for several."""
    found = set(_MEETING_ID.findall(request))
    return found.pop() if len(found) == 1 else None


def _read(toolbox: Toolbox, name: str, meeting: dict[str, Any]) -> ToolResult | None:
    if name not in toolbox.describe():
        return None  # not shipped yet: no call, no budget spent
    if name not in OPTIONAL:
        return toolbox.call(name, **meeting)
    try:
        return toolbox.call(name, **meeting)
    except Exception as exc:  # a bug in C's or D's tool costs its section only
        log.warning("report_optional_tool_failed tool=%s error=%s", name, type(exc).__name__)
        return None


def _failed(reason: str) -> SubagentState:
    return {"outcome": SubagentResult(result=ToolResult.failure(reason))}


def build(toolbox: Toolbox) -> CompiledSubagent:
    def report(state: SubagentState) -> SubagentState:
        request = state.get("request", "")
        if request == TRIGGER:
            meeting: dict[str, Any] = {}  # the run's scope carries it
        else:
            meeting_id = _meeting_id(request)
            if meeting_id is None:
                return _failed("the request names no single meeting id")
            meeting = {"meeting_id": meeting_id}

        results = {name: _read(toolbox, name, meeting) for name in TOOLS}
        actions = results[ACTIONS_TOOL]
        if actions is not None and not actions.ok:
            # B (or the scope check) cannot find the meeting: nothing to report.
            return {"outcome": SubagentResult(result=actions)}
        body = render(actions, results[REVIEW_TOOL], results[GAPS_TOOL], results[LINKS_TOOL])
        if not body:
            return _failed("nothing to report for this meeting")

        # No team_id anywhere: the run's scope fills it (E's RUN_SCOPE).
        draft = ProposedAction(
            kind="meeting_report_draft",
            title="회의 리포트 초안 저장",
            tool=DRAFT_ACTION,
            arguments={
                **meeting,
                "body_markdown": body,
                "pending_review": has_pending(results[REVIEW_TOOL]),
            },
            level="L1",
            rationale="The meeting's analysis finished; store its structured minutes.",
        )
        post = ProposedAction(
            kind="meeting_report_post",
            title="회의 리포트 게시",
            tool=PUBLISH_ACTION,
            arguments=dict(meeting),
            level="L2",
            rationale="Post the stored minutes to the team channel once a person approves.",
        )
        summary = ToolResult(ok=True, summary="회의 리포트 초안을 만들었습니다.", items=[])
        return {"outcome": SubagentResult(result=summary, proposed=[draft, post])}

    # One node: the steps are a straight line with nothing to branch on. Split
    # when an LLM summary step (option C of the design) is added.
    graph = StateGraph(SubagentState)
    graph.add_node("report", report)
    graph.add_edge(START, "report")
    graph.add_edge("report", END)
    return graph.compile()
