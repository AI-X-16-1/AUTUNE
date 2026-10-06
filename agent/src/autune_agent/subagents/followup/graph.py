"""The Follow-up subgraph (spec section 3): read, decide, propose.

Three nodes, no model call, no checkpointer (agent/CLAUDE.md rule 6). It reads
through its ``Toolbox`` only and calls no write: the follow-up meeting leaves as
one L2 ``ProposedAction`` for plan mode, where an approver with scope
``followup`` -- the team lead -- accepts or refuses it.

**Ids only in the proposal.** Plan mode queues an L2 proposal only when its
arguments are ids, dates, booleans and short enums (#556), so the item's
wording is B's to write and the lead sees the gap titles through the
approvals-page preview (#562), never through the arguments.

M is the run's meeting when its scope has one -- the trigger's, or the screen a
chat was asked from -- and otherwise the team's latest analysed meeting.

**A suggested date.** The proposal carries ``due_date``: the team's usual gap
between meetings after its latest one (``rules.suggest_date``), read from the
team's meeting days and nothing else. The lead sees it on the card and moves it
on the board; the item's due date is what B puts on a calendar (#441).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from autune_agent.main.registry import NO_MEETING, Toolbox
from autune_agent.main.subagents import CompiledSubagent, SubagentState
from autune_agent.results import ProposedAction, SubagentResult, ToolResult

from . import rules

OPEN_GAPS = "gap.open_gaps"
RECURRING = "gap.recurring_open_gaps"
QUESTIONS = "extraction.unresolved_questions"
RECENT = "audio.recent_meetings"
OPEN_ITEM = "extraction.open_followup_item"
"""Whether the team has a Follow-up item still open (#561). A failed read
proposes nothing -- an unknown is not "none open"."""
WRITE = "extraction.add_followup_item"
"""B's L2 write for the item (#561). It takes the meeting and writes the fixed
wording itself, so the proposal carries ids only. Not ``add_action_item``: that
is L1 since #576 and would run without the lead's approval."""

TOOLS = (OPEN_GAPS, RECURRING, QUESTIONS, RECENT, OPEN_ITEM)
ANALYSED = ("awaiting_confirmation", "complete", "delivered")
"""Meeting statuses after the pipeline's analysis, as Research reads them."""
KST = ZoneInfo("Asia/Seoul")
"""The team's calendar day, as A's tools write a meeting's time."""


class FollowupState(SubagentState, total=False):
    at: dict[str, str]
    """``{"meeting_id": M}`` when M was picked here; empty when the scope holds it."""
    open_gaps: ToolResult
    recurring: ToolResult
    questions: ToolResult
    recent: ToolResult
    """The team's meetings, when the run had to read them to pick M."""
    verdict: rules.Verdict


def _stop(reason: str, summary: str) -> dict[str, Any]:
    return {"outcome": SubagentResult(result=ToolResult.failure(reason, summary))}


def _done(summary: str) -> dict[str, Any]:
    return {"outcome": SubagentResult(result=ToolResult(ok=True, summary=summary))}


def _today() -> date:
    return datetime.now(KST).date()


def _held(recent: ToolResult | None) -> list[date]:
    """The days the team's past meetings started on, from ``started_at``.

    A meeting with no start time, or one still ahead, has not set the team's
    rhythm. An unreadable list is no days, so the suggestion falls back to a
    fixed few business days rather than holding the proposal back.
    """
    if recent is None or not recent.ok:
        return []
    now = datetime.now(UTC)
    days = []
    for item in recent.items:
        raw = (item.model_extra or {}).get("started_at")
        if not isinstance(raw, str):
            continue
        try:
            started = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if started <= now:
            days.append(started.astimezone(KST).date())
    return days


def _day(value: date) -> str:
    return f"{value.month}월 {value.day}일({'월화수목금토일'[value.weekday()]})"


def build(toolbox: Toolbox) -> CompiledSubagent:
    def read(state: FollowupState) -> dict[str, Any]:
        at: dict[str, str] = {}
        recent: ToolResult | None = None
        gaps = toolbox.call(OPEN_GAPS)
        if not gaps.ok and gaps.reason == NO_MEETING:
            recent = toolbox.call(RECENT)
            if not recent.ok:
                return _stop(
                    recent.reason or "recent meetings unreadable",
                    "최근 회의 목록을 읽지 못했습니다.",
                )
            picked = next((i for i in recent.items if getattr(i, "status", None) in ANALYSED), None)
            meeting_id = (picked.model_extra or {}).get("meeting_id") if picked else None
            if not isinstance(meeting_id, str):
                return _stop("no analysed meeting", "후속 회의를 판단할 분석된 회의가 없습니다.")
            at = {"meeting_id": meeting_id}
            gaps = toolbox.call(OPEN_GAPS, **at)
        if not gaps.ok:
            return _stop(gaps.reason or "gaps unreadable", "회의의 갭을 읽지 못했습니다.")
        recurring = toolbox.call(RECURRING, **at)
        if not recurring.ok:
            return _stop(
                recurring.reason or "recurring gaps unreadable",
                "이어서 열린 항목을 읽지 못했습니다.",
            )
        questions = toolbox.call(QUESTIONS, **at)
        if not questions.ok:
            return _stop(questions.reason or "questions unreadable", "질문을 읽지 못했습니다.")
        read: dict[str, Any] = {"open_gaps": gaps, "recurring": recurring, "questions": questions}
        if recent is not None:
            read["recent"] = recent
        return {"at": at, **read}

    def decide(state: FollowupState) -> dict[str, Any]:
        verdict = rules.decide(state["open_gaps"], state["recurring"], state["questions"])
        if not verdict.fires:
            return _done("후속 회의가 필요해 보이지 않습니다.")
        # Read only when the rule fires: otherwise the call buys nothing.
        open_item = toolbox.call(OPEN_ITEM)
        if not open_item.ok:
            return _stop(
                open_item.reason or "follow-up items unreadable",
                "열린 후속 회의 항목을 확인하지 못해 제안하지 않았습니다.",
            )
        if open_item.items:
            return _done("이미 열린 후속 회의 항목이 있어 새로 제안하지 않았습니다.")
        return {"verdict": verdict}

    def propose(state: FollowupState) -> dict[str, Any]:
        verdict = state["verdict"]
        reason = verdict.reason()
        # Read here, not in ``read``: a run that proposes nothing does not spend it.
        recent = state.get("recent") or toolbox.call(RECENT)
        suggested = rules.suggest_date(_held(recent), _today())
        result = ToolResult(
            ok=True,
            summary=(
                f"후속 회의를 제안했습니다 ({reason}). 추천 날짜는 {_day(suggested)}입니다. "
                "팀장이 승인하면 보드에 항목이 생깁니다."
            ),
            items=rules.cited(state["open_gaps"], verdict),
            evidence=verdict.evidence,
        )
        proposal = ProposedAction(
            kind="followup_meeting",
            title="후속 회의 제안",
            tool=WRITE,
            arguments={**state["at"], "due_date": suggested.isoformat()},
            level="L2",
            rationale=f"{reason}. 추천 날짜 {_day(suggested)}.",
            evidence=verdict.evidence,
        )
        return {"outcome": SubagentResult(result=result, proposed=[proposal])}

    def next_after(node: str) -> Any:
        return lambda s: END if "outcome" in s else node

    graph = StateGraph(FollowupState)
    graph.add_node("read", read)
    graph.add_node("decide", decide)
    graph.add_node("propose", propose)
    graph.add_edge(START, "read")
    graph.add_conditional_edges("read", next_after("decide"), ["decide", END])
    graph.add_conditional_edges("decide", next_after("propose"), ["propose", END])
    graph.add_edge("propose", END)
    return cast(CompiledSubagent, graph.compile())
