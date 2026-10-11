"""The Briefing subagent's graph: find the meeting, read four tools, render sections.

Fixed tool order, no LLM, and **no write**: the outcome carries no proposal.
The ten-minutes-before brief is D's surface and D sends it (agent-layer.md
section 8 rule 2), so this subagent only *composes* the fuller picture a person
asks for -- it never posts, and nothing it returns is stored as text (rule 8).

What it reads, in order:

1. ``context.brief_recap`` -- the earlier meeting D chose for this one's brief,
   and what it decided. D chooses it ten minutes before the start, so before
   then the section says so rather than guess a meeting.
2. ``context.brief_agenda`` -- the team's open Jira issues, as B reported them.
3. ``gap.open_gaps`` on **that earlier meeting** -- what nobody dismissed there.
   Skipped when D has not chosen one.
4. ``extraction.open_action_items`` -- confirmed work that is late or due soon.

Where the meeting comes from, the same three cases as the Report subagent:

- **The request names no ``mtg_...`` id:** the run's scope carries the meeting
  (asked on a meeting's screen), so the tool calls do not name it and the
  Toolbox fills it in. A run about no meeting gets the Toolbox's own refusal.
- **Exactly one id:** it is passed on, and the scope still holds it to the
  run's team.
- **Several different ids:** refused rather than guessed.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langgraph.graph import END, START, StateGraph

from autune_agent.main import BudgetExceededError, SubagentState, Toolbox
from autune_agent.main.subagents import CompiledSubagent
from autune_agent.results import Finding, SubagentResult, ToolResult

from .render import (
    actions_section,
    agenda_section,
    gaps_section,
    previous_meeting_id,
    previous_meeting_title,
    recap_section,
)

log = logging.getLogger(__name__)

RECAP_TOOL = "context.brief_recap"
AGENDA_TOOL = "context.brief_agenda"
GAPS_TOOL = "gap.open_gaps"
ACTIONS_TOOL = "extraction.open_action_items"
TOOLS = (RECAP_TOOL, AGENDA_TOOL, GAPS_TOOL, ACTIONS_TOOL)
"""A tool that is not registered is skipped, so a wrong name here loses a section
silently -- a test pins every one to its module's registry."""

NOT_COMPOSED = "brief not composed yet"
"""D's reason while it has not yet chosen the earlier meeting
(``autune_context.tools.BRIEF_NOT_COMPOSED``; a test pins the two together)."""

# ASCII boundaries, not \b: in a str pattern \b is Unicode-aware, so a Korean
# particle right after the id ("mtg_ab12cd의") would count as part of the word.
_MEETING_ID = re.compile(r"(?<![A-Za-z0-9_])mtg_[A-Za-z0-9]+(?![A-Za-z0-9_])")


def _read(toolbox: Toolbox, name: str, arguments: dict[str, Any]) -> ToolResult | None:
    """The tool's answer, or ``None`` when it is not shipped or broke.

    Every read is context around the others: a bug in one module's tool costs
    its section and nothing else. The run's budget stop is the one error that
    is not swallowed -- it ends the run.
    """
    if name not in toolbox.describe():
        return None  # not shipped yet: no call, no budget spent
    try:
        return toolbox.call(name, **arguments)
    except BudgetExceededError:
        raise
    except Exception as exc:  # noqa: BLE001 -- a module's bug must not fail the brief
        log.warning("briefing_tool_failed tool=%s error=%s", name, type(exc).__name__)
        return None


def _failed(reason: str) -> SubagentState:
    return {"outcome": SubagentResult(result=ToolResult.failure(reason))}


def _summary(recap: ToolResult | None, *, not_composed: bool, missing: list[str]) -> str:
    title = previous_meeting_title(recap)
    parts = ["회의 브리프를 정리했습니다."]
    if not_composed:
        parts.append("이어받는 회의는 아직 정해지지 않았습니다.")
    elif title:
        parts.append(f"지난 회의 '{title}'를 이어받습니다.")
    if missing:
        parts.append(f"읽지 못한 부분: {', '.join(missing)}.")
    return " ".join(parts)


def build(toolbox: Toolbox) -> CompiledSubagent:
    def brief(state: SubagentState) -> SubagentState:
        named = set(_MEETING_ID.findall(state.get("request", "")))
        if len(named) > 1:
            return _failed("the request names several meetings")
        # None named: the run's scope carries the meeting (or the Toolbox refuses).
        meeting: dict[str, Any] = {"meeting_id": named.pop()} if named else {}

        recap = _read(toolbox, RECAP_TOOL, meeting)
        not_composed = recap is not None and not recap.ok and recap.reason == NOT_COMPOSED
        if recap is not None and not recap.ok and not not_composed:
            # D (or the scope check) cannot find the meeting: nothing to brief.
            return {"outcome": SubagentResult(result=recap)}

        agenda = _read(toolbox, AGENDA_TOOL, meeting)
        earlier = previous_meeting_id(recap)
        # The earlier meeting's gaps. The Toolbox holds the id to the run's team.
        gaps = _read(toolbox, GAPS_TOOL, {"meeting_id": earlier}) if earlier else None
        actions = _read(toolbox, ACTIONS_TOOL, {})

        sections: list[tuple[str, Finding | None, ToolResult | None]] = [
            ("지난 회의", recap_section(recap, not_composed=not_composed), recap),
            ("Jira 안건", agenda_section(agenda), agenda),
            ("할 일", actions_section(actions), actions),
            ("닫히지 않은 갭", gaps_section(gaps), gaps),
        ]
        findings = [section for _, section, _ in sections if section is not None]
        if not findings:
            return _failed("nothing to brief for this meeting")

        # Not "asked and found nothing": a tool that is absent or failed, or a gap
        # read that was skipped because there was no earlier meeting to ask about.
        missing = [
            name
            for name, section, _ in sections
            if section is None and not (name == "닫히지 않은 갭" and earlier is None)
        ]
        used = [r for _, _, r in sections if r is not None and r.ok]
        result = ToolResult(
            ok=True,
            summary=_summary(recap, not_composed=not_composed, missing=missing),
            items=findings,
            evidence=list(dict.fromkeys(e for r in used for e in r.evidence)),
            confidence=min((r.confidence for r in used), default=1.0),
            truncated=any(r.truncated for r in used),
        )
        return {"outcome": SubagentResult(result=result)}

    # One node: a straight line with nothing to branch on. Split when an LLM
    # step (a tighter wording of a long section) is added.
    graph = StateGraph(SubagentState)
    graph.add_node("brief", brief)
    graph.add_edge(START, "brief")
    graph.add_edge("brief", END)
    return graph.compile()
