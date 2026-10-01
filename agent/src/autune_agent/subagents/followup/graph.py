"""The Follow-up subgraph: read what the meeting left open, decide, propose.

``agent/docs/specs/2026-09-30-followup-subagent-design.md`` section 3. Three
nodes, no model call, no checkpointer (agent/CLAUDE.md rule 6). It reads
through its ``Toolbox`` only and calls no write. A follow-up leaves as one L2
``ProposedAction`` for the main agent to put through plan mode, where an
approver with scope ``followup`` -- the team lead -- accepts or refuses it.

``team_id`` is never passed here: the run's scope fills it (C's and B's
``RUN_SCOPE``). ``meeting_id`` is the scope's too on a trigger. On a chat run
the scope has none, and the subgraph picks the team's most recent analysed
meeting, as Research does.
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from autune_agent.main.registry import NO_MEETING, Toolbox
from autune_agent.main.subagents import CompiledSubagent, SubagentState
from autune_agent.results import Finding, ProposedAction, SubagentResult, ToolResult

from . import rules

OPEN = "gap.open_gaps"
RECURRING = "gap.recurring_open_gaps"
QUESTIONS = "extraction.unresolved_questions"
RECENT = "audio.recent_meetings"
ADD_ITEM = "extraction.add_action_item"
"""The write the proposal names. B's ``ACTIONS``, L2; the main agent runs it
only after the lead approves."""

TOOLS = (OPEN, RECURRING, QUESTIONS, RECENT)

ANALYSED = ("awaiting_confirmation", "complete", "delivered")
"""A's meeting statuses after the pipeline has run."""


class FollowupState(SubagentState, total=False):
    meeting_id: str
    open_gaps: list[rules.Gap]
    carried: list[rules.Gap]
    carried_evidence: list[str]
    questions: int


def _stop(reason: str, summary: str) -> dict[str, Any]:
    return {"outcome": SubagentResult(result=ToolResult.failure(reason, summary))}


def _done(summary: str) -> dict[str, Any]:
    return {"outcome": SubagentResult(result=ToolResult(ok=True, summary=summary))}


def build(toolbox: Toolbox) -> CompiledSubagent:
    def read(state: FollowupState) -> dict[str, Any]:
        opened = toolbox.call(OPEN)
        meeting_id: str | None = None
        if not opened.ok and opened.reason == NO_MEETING:
            recent = toolbox.call(RECENT)
            picked = next((i for i in recent.items if getattr(i, "status", None) in ANALYSED), None)
            if picked is None:
                return _stop("no analysed meeting", "살펴볼 회의가 없습니다.")
            meeting_id = (picked.model_extra or {}).get("meeting_id")
            opened = toolbox.call(OPEN, meeting_id=meeting_id)
        if not opened.ok:
            return _stop(opened.reason or "gaps unreadable", "회의의 갭을 읽지 못했습니다.")

        scoped = {"meeting_id": meeting_id} if meeting_id else {}
        recurring = toolbox.call(RECURRING, **scoped)
        if not recurring.ok:
            return _stop(
                recurring.reason or "recurrence unreadable", "직전 회의와 비교하지 못했습니다."
            )
        asked = toolbox.call(QUESTIONS, **scoped)
        if not asked.ok:
            return _stop(asked.reason or "questions unreadable", "회의의 질문을 읽지 못했습니다.")
        return {
            "open_gaps": rules.gaps_from(opened.items),
            "carried": rules.gaps_from(recurring.items),
            "carried_evidence": list(recurring.evidence),
            "questions": len(asked.items),
            **scoped,
        }

    def propose(state: FollowupState) -> dict[str, Any]:
        decision = rules.decide(
            state["open_gaps"], state["carried"], state["carried_evidence"], state["questions"]
        )
        if decision is None:
            return _done("후속 회의가 필요해 보이지 않습니다.")
        description = rules.item_description(decision)
        why = rules.rationale(decision)
        arguments: dict[str, Any] = {"description": description}
        if "meeting_id" in state:
            # A chat run's meeting; on a trigger the scope fills it in.
            arguments["meeting_id"] = state["meeting_id"]
        result = ToolResult(
            ok=True,
            summary=f"{why} 팀장이 승인하면 보드에 후속 회의 항목이 생깁니다.",
            items=[
                Finding.model_validate(
                    {"title": g.title, "score": g.score, "id": g.id, "severity": g.severity}
                )
                for g in decision.gaps
            ],
            evidence=list(decision.evidence),
        )
        proposed = [
            ProposedAction(
                kind="propose_followup_meeting",
                title=description,
                tool=ADD_ITEM,
                arguments=arguments,
                level="L2",
                rationale=why,
                evidence=list(decision.evidence),
            )
        ]
        return {"outcome": SubagentResult(result=result, proposed=proposed)}

    def after_read(state: FollowupState) -> str:
        return END if "outcome" in state else "propose"

    graph = StateGraph(FollowupState)
    graph.add_node("read", read)
    graph.add_node("propose", propose)
    graph.add_edge(START, "read")
    graph.add_conditional_edges("read", after_read, ["propose", END])
    graph.add_edge("propose", END)
    return cast(CompiledSubagent, graph.compile())
