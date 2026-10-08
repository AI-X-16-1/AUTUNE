"""The Follow-up subgraph (spec section 3): read, decide, propose.

Three nodes, no checkpointer (agent/CLAUDE.md rule 6), and no model call in
the decision or the date; a model writes only the sentence saying why. It reads
through its ``Toolbox`` only and calls no write: the follow-up meeting leaves as
one L2 ``ProposedAction`` for plan mode, where an approver with scope
``followup`` -- the team lead -- accepts or refuses it.

**Ids only in the proposal.** Plan mode queues an L2 proposal only when its
arguments are ids, dates, booleans and short enums (#556), so the item's
wording is B's to write and the lead sees the gap titles through the
approvals-page preview (#562), never through the arguments.

M is the run's meeting when its scope has one -- the trigger's, or the screen a
chat was asked from -- and otherwise the team's latest analysed meeting.

**A suggested date.** The proposal carries ``due_date``: just after most of
M's action items are due (``rules.suggest_from_due_dates``, #963), or, when M
has no usable due date or B's read fails, the team's usual gap between meetings
after its latest one (``rules.suggest_date``). Business days skip weekends
and Korea's public holidays as B knows them (#964). ``basis`` says which, so the
card can mark a date resting on drafts "초안 기준". A sentence says why the
date is that date (``explain``): a model writes it from what the rule used,
once, here, and never chooses or moves the date. From B it reads due dates
and whether each is confirmed, never who owns an item (spec section 6). The
lead sees the date on the card and moves it on the board; the item's due date
is what B puts on a calendar (#441).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from autune_agent.main.registry import NO_MEETING, Toolbox
from autune_agent.main.subagents import CompiledSubagent, SubagentState
from autune_agent.results import ProposedAction, SubagentResult, ToolResult

from . import explain, rules

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

DUE_DATES = "extraction.meeting_due_dates"
"""M's open dated action items as due dates and confirmation flags, on one row
(#963; B's side is #966). B hands over confirmed items only, so right after a
meeting the date is mostly the rhythm's. Until B ships it the call fails, and
the date falls back to the team's rhythm -- the proposal never waits on it."""

HOLIDAYS = "extraction.public_holidays"
"""Korea's public holidays in a range of days, from B's own source (Google's
public holiday calendar, B's table in code when it has not been read; #964,
#985). Dates of public record, nothing about anybody. Until B ships it the
call fails and business days skip weekends only."""

HOLIDAYS_AHEAD = timedelta(days=45)
"""How far from today the holidays are read: past the 14-day horizon and the
longest run of days off (설 or 추석 with a weekend and a substitute day)."""

TOOLS = (OPEN_GAPS, RECURRING, QUESTIONS, RECENT, OPEN_ITEM, DUE_DATES, HOLIDAYS)
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


def _due(result: ToolResult) -> list[rules.Due]:
    """The due dates on B's row, as ``{"date": ISO, "confirmed": bool}``, and a
    confirmed entry's ``title`` when the row holds one (B does not hand one over
    yet; the reason sentence then counts the items without naming one).

    A failed read, or a row without the list, is no dates, so the suggestion
    falls back to the rhythm. An entry that is not a date and a flag is
    skipped rather than guessed at. Any other key but ``title`` is ignored: the
    rule takes a day and a flag and nothing else; a title only reaches the
    reason sentence.
    """
    if not result.ok:
        return []
    due: list[rules.Due] = []
    for item in result.items:
        entries = (item.model_extra or {}).get("due_dates")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            raw, confirmed = entry.get("date"), entry.get("confirmed")
            if not isinstance(raw, str) or not isinstance(confirmed, bool):
                continue
            title = entry.get("title")
            try:
                due.append(
                    rules.Due(
                        date.fromisoformat(raw),
                        confirmed,
                        title if confirmed and isinstance(title, str) and title.strip() else None,
                    )
                )
            except ValueError:
                continue
    return due


def _holidays(result: ToolResult) -> frozenset[date]:
    """The days on B's row, as ISO dates under ``days``.

    A failed read, or a row without the list, is no holidays: weekends are
    still skipped, so the date is no worse than before #964. An entry that is
    not an ISO date is skipped.
    """
    if not result.ok:
        return frozenset()
    days: set[date] = set()
    for item in result.items:
        entries = (item.model_extra or {}).get("days")
        if not isinstance(entries, list):
            continue
        for raw in entries:
            if not isinstance(raw, str):
                continue
            try:
                days.add(date.fromisoformat(raw))
            except ValueError:
                continue
    return frozenset(days)


BASIS_NOTE = {"confirmed": "확정 기한 기준", "draft": "초안 기준", "cadence": "회의 주기 기준"}


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
        # Read here, not in ``read``: a run that proposes nothing does not spend
        # them. The meeting list only when the due dates leave no date.
        today = _today()
        off = _holidays(
            toolbox.call(
                HOLIDAYS,
                start=today.isoformat(),
                end=(today + HOLIDAYS_AHEAD).isoformat(),
            )
        )
        suggestion = rules.suggest_from_due_dates(
            _due(toolbox.call(DUE_DATES, **state["at"])), today, off
        )
        if suggestion is None:
            recent = state.get("recent") or toolbox.call(RECENT)
            suggestion = rules.suggest_by_rhythm(_held(recent), today, off)
        suggested, basis = suggestion.day, suggestion.basis
        when = f"{_day(suggested)}, {BASIS_NOTE[basis]}"
        why = explain.explain(suggestion).text
        result = ToolResult(
            ok=True,
            summary=(
                f"후속 회의를 제안했습니다 ({reason}). 추천 날짜는 {when}입니다. "
                f"{why} 팀장이 승인하면 보드에 항목이 생깁니다."
            ),
            items=rules.cited(state["open_gaps"], verdict),
            evidence=verdict.evidence,
        )
        proposal = ProposedAction(
            kind=verdict.kind,
            title="후속 회의 제안",
            tool=WRITE,
            # ``basis`` is a short enum, so plan mode queues it, and the card
            # reads it off the row; B's write must declare it (#963).
            arguments={**state["at"], "due_date": suggested.isoformat(), "basis": basis},
            level="L2",
            rationale=f"{reason}. 추천 날짜 {when}. {why}",
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
