"""Follow-up: read, decide and propose (spec sections 3, 4 and 8).

The mocks return what C's ``open_gaps`` and ``recurring_open_gaps`` (#546) and
B's ``unresolved_questions`` and ``open_followup_item`` (#561) return.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autune_agent.main import CallBudget, RunScope, Toolbox, collect_actions, collect_subagents
from autune_agent.main.pending import arguments_ok
from autune_agent.main.registry import Tool
from autune_agent.subagents.followup import SUBAGENT, graph, rules
from autune_agent.subagents.followup.graph import (
    DUE_DATES,
    HOLIDAYS,
    OPEN_GAPS,
    OPEN_ITEM,
    QUESTIONS,
    RECENT,
    RECURRING,
    WRITE,
)
from autune_contracts import INTELLIGENCE_COMPLETED

QUESTION_TEXT = "결제 실패하면 누가 책임지죠?"
MONDAY = date(2026, 10, 5)


@pytest.fixture(autouse=True)
def _monday(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every run is on Monday 2026-10-05, so a suggested date is fixed."""
    monkeypatch.setattr(graph, "_today", lambda: MONDAY)


def gap(gid: str, severity: str = "high", key: str = "risk") -> dict[str, Any]:
    return {
        "id": gid,
        "title": f"{key} — 결제",
        "body": "누가 확인하나요?",
        "score": 0.9 if severity == "high" else 0.4,
        "severity": severity,
        "template_item_key": key,
        "topics": ["결제"],
    }


def carried(gid: str, key: str = "risk") -> dict[str, Any]:
    return {
        "id": gid,
        "title": f"{key} — 결제",
        "score": 0.9,
        "severity": "high",
        "template_item_key": key,
        "previous_meeting_id": "mtg_before",
        "previous_gap_id": "gap_before",
    }


def _result(items: list[dict[str, Any]], ok: bool = True) -> dict[str, Any]:
    return {
        "ok": ok,
        "reason": None if ok else "unreadable",
        "summary": "결과",
        "items": items,
        "evidence": [i["id"] for i in items if "id" in i],
    }


def tools_for(
    *,
    gaps: list[dict[str, Any]] | None = None,
    recurring: list[dict[str, Any]] | None = None,
    questions: int = 1,
    open_items: list[dict[str, Any]] | None = None,
    recent: list[dict[str, Any]] | None = None,
    recent_ok: bool = True,
    gaps_ok: bool = True,
    with_open_item: bool = True,
    due: list[dict[str, Any]] | None = None,
    due_ok: bool = True,
    with_due: bool = True,
    off: list[Any] | None = None,
    off_ok: bool = True,
    with_holidays: bool = True,
    calls: list[tuple[str, dict[str, Any]]] | None = None,
    holiday_spans: list[tuple[str, str]] | None = None,
) -> dict[str, Tool]:
    log = [] if calls is None else calls
    spans = [] if holiday_spans is None else holiday_spans

    def open_gaps(session: Any, team_id: str, meeting_id: str) -> dict[str, Any]:
        log.append((OPEN_GAPS, {"meeting_id": meeting_id}))
        return _result(gaps or [], ok=gaps_ok)

    def recurring_open_gaps(session: Any, team_id: str, meeting_id: str) -> dict[str, Any]:
        log.append((RECURRING, {"meeting_id": meeting_id}))
        return _result(recurring or [])

    def unresolved(session: Any, meeting_id: str) -> dict[str, Any]:
        log.append((QUESTIONS, {"meeting_id": meeting_id}))
        items = [
            {"title": "질문", "body": QUESTION_TEXT, "id": f"utt_q{n}"} for n in range(questions)
        ]
        return _result(items)

    def recent_meetings(session: Any, team_id: str) -> dict[str, Any]:
        log.append((RECENT, {}))
        return {"ok": recent_ok, "summary": "회의", "items": recent or [], "evidence": []}

    def open_followup_item(session: Any, team_id: str) -> dict[str, Any]:
        log.append((OPEN_ITEM, {}))
        return _result(open_items or [])

    def meeting_due_dates(session: Any, team_id: str, meeting_id: str) -> dict[str, Any]:
        log.append((DUE_DATES, {"meeting_id": meeting_id}))
        rows = [{"title": "기한", "due_dates": due}] if due is not None else []
        return {"ok": due_ok, "summary": "기한", "items": rows, "evidence": []}

    def public_holidays(session: Any, start: str, end: str) -> dict[str, Any]:
        log.append((HOLIDAYS, {}))
        spans.append((start, end))
        rows = [{"title": "공휴일", "days": off}] if off is not None else []
        return {"ok": off_ok, "summary": "공휴일", "items": rows, "evidence": []}

    tools = {
        OPEN_GAPS: Tool(name=OPEN_GAPS, description="Use this in tests.", fn=open_gaps),
        RECURRING: Tool(name=RECURRING, description="Use this in tests.", fn=recurring_open_gaps),
        QUESTIONS: Tool(name=QUESTIONS, description="Use this in tests.", fn=unresolved),
        RECENT: Tool(name=RECENT, description="Use this in tests.", fn=recent_meetings),
    }
    if with_open_item:
        tools[OPEN_ITEM] = Tool(
            name=OPEN_ITEM, description="Use this in tests.", fn=open_followup_item
        )
    if with_holidays:
        tools[HOLIDAYS] = Tool(name=HOLIDAYS, description="Use this in tests.", fn=public_holidays)
    if with_due:
        tools[DUE_DATES] = Tool(
            name=DUE_DATES, description="Use this in tests.", fn=meeting_due_dates
        )
    return tools


def invoke(
    tools: dict[str, Tool],
    *,
    session: Session,
    team_id: str,
    meeting: str | None,
    request: str = INTELLIGENCE_COMPLETED,
) -> Any:
    box = Toolbox(
        tools,
        session,
        CallBudget(),
        allowed=SUBAGENT.tools,
        scope=RunScope(team_id=team_id, meeting_id=meeting),
    )
    return SUBAGENT.build(box).invoke({"request": request})["outcome"]


def names(calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [name for name, _ in calls]


def test_it_is_collected_woken_by_intelligence_completed_and_reads_only_its_list() -> None:
    assert collect_subagents()["followup"] is SUBAGENT
    assert SUBAGENT.triggers == (INTELLIGENCE_COMPLETED,)
    assert set(SUBAGENT.tools) == {
        OPEN_GAPS,
        RECURRING,
        QUESTIONS,
        RECENT,
        OPEN_ITEM,
        DUE_DATES,
        HOLIDAYS,
    }


def test_it_reads_nothing_about_a_person() -> None:
    for tool in SUBAGENT.tools:
        assert not any(word in tool for word in ("speaking", "participation", "silent", "person"))


def test_a_carried_over_item_is_one_l2_proposal_on_the_trigger_meeting(session, team) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    tools = tools_for(
        gaps=[gap("gap_now", "medium")], recurring=[carried("gap_now")], questions=0, calls=calls
    )

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    (proposal,) = outcome.proposed
    assert (proposal.level, proposal.tool, proposal.kind) == ("L2", WRITE, "followup_reopened")
    # The trigger's meeting is the run's: the action is bound to it when it runs.
    # No meeting days to read, so three business days from Monday.
    assert proposal.arguments == {"due_date": "2026-10-08", "basis": "cadence"}
    assert proposal.evidence == ["gap_now"]
    assert "다시 열린 항목 1개" in outcome.result.summary
    assert "10월 8일(목)" in outcome.result.summary
    assert names(calls) == [OPEN_GAPS, RECURRING, QUESTIONS, OPEN_ITEM, HOLIDAYS, DUE_DATES, RECENT]
    assert {args["meeting_id"] for _, args in calls if args} == {team["meeting"]}


def test_the_write_is_one_b_declares_l2_and_takes_the_meeting_a_date_and_a_basis() -> None:
    """A proposal cannot demote a write, but an L1 one would run with no lead at all.
    And an argument the write does not take fails the approval (``bind_scope``)."""
    write = collect_actions(["extraction"])[WRITE]
    assert write.level == "L2"
    assert write.parameters == {"team_id", "meeting_id", "due_date", "basis"}


def test_every_proposal_passes_plan_modes_argument_rule(session, team) -> None:
    tools = tools_for(gaps=[gap("gap_1"), gap("gap_2")], recurring=[carried("gap_1")])

    (proposal,) = invoke(
        tools, session=session, team_id=team["team"], meeting=team["meeting"]
    ).proposed

    assert arguments_ok(proposal.arguments)


def test_two_high_gaps_and_a_question_leave_a_meeting_heavy(session, team) -> None:
    tools = tools_for(gaps=[gap("gap_1"), gap("gap_2", key="dependency")], questions=1)

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    (proposal,) = outcome.proposed
    assert proposal.evidence == ["gap_1", "gap_2"]
    assert "높음 갭 2건과 미해결 질문" in proposal.rationale
    assert proposal.kind == "followup_risky"


def test_high_gaps_without_a_question_are_not_heavy(session, team) -> None:
    tools = tools_for(gaps=[gap("gap_1"), gap("gap_2", key="dependency")], questions=0)

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    assert outcome.result.ok is True
    assert outcome.proposed == []


def test_one_high_gap_and_a_question_are_not_heavy(session, team) -> None:
    tools = tools_for(gaps=[gap("gap_1"), gap("gap_2", "medium")], questions=2)

    assert (
        invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"]).proposed == []
    )


def test_both_rules_cite_each_gap_once_carried_first(session, team) -> None:
    tools = tools_for(
        gaps=[gap("gap_1"), gap("gap_2", key="dependency")],
        recurring=[carried("gap_2", "dependency")],
    )

    (proposal,) = invoke(
        tools, session=session, team_id=team["team"], meeting=team["meeting"]
    ).proposed

    assert proposal.evidence == ["gap_2", "gap_1"]
    assert proposal.kind == "followup_reopened_risky"


def test_nothing_fires_and_the_open_item_read_is_not_spent(session, team) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    tools = tools_for(gaps=[gap("gap_1", "medium")], calls=calls)

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    assert outcome.result.ok is True
    assert outcome.proposed == []
    assert "필요해 보이지 않습니다" in outcome.result.summary
    assert OPEN_ITEM not in names(calls)


def test_an_open_follow_up_item_stops_another_proposal(session, team) -> None:
    tools = tools_for(
        recurring=[carried("gap_1")],
        open_items=[{"id": "act_followup", "title": "후속 회의 잡기"}],
    )

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    assert outcome.result.ok is True
    assert outcome.proposed == []
    assert "이미 열린" in outcome.result.summary


def test_an_unknown_open_item_proposes_nothing(session, team) -> None:
    # Until B ships the read (#561) the call fails; not knowing is not "none open".
    tools = tools_for(recurring=[carried("gap_1")], with_open_item=False)

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    assert outcome.result.ok is False
    assert outcome.proposed == []


def test_an_unreadable_meeting_fails_without_a_proposal(session, team) -> None:
    tools = tools_for(recurring=[carried("gap_1")], gaps_ok=False)

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    assert outcome.result.ok is False
    assert outcome.proposed == []


def test_a_chat_run_takes_the_latest_analysed_meeting(session, team) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    tools = tools_for(
        recurring=[carried("gap_1")],
        recent=[
            {"title": "진행 중", "meeting_id": "mtg_live", "status": "analyzing"},
            {"title": "주간 회의", "meeting_id": team["meeting"], "status": "complete"},
        ],
        calls=calls,
    )

    outcome = invoke(
        tools, session=session, team_id=team["team"], meeting=None, request="후속 회의 필요해?"
    )

    (proposal,) = outcome.proposed
    # A chat run's scope has no meeting, so the proposal names the one it read.
    assert proposal.arguments == {
        "meeting_id": team["meeting"],
        "due_date": "2026-10-08",
        "basis": "cadence",
    }
    assert arguments_ok(proposal.arguments)
    # The first open_gaps is refused by the scope (no meeting) before it reaches C.
    # The meeting list it read to pick M also gives the date: no second read.
    assert names(calls) == [RECENT, OPEN_GAPS, RECURRING, QUESTIONS, OPEN_ITEM, HOLIDAYS, DUE_DATES]
    assert {args["meeting_id"] for _, args in calls if args} == {team["meeting"]}


def test_a_chat_run_with_no_analysed_meeting_says_so(session, team) -> None:
    tools = tools_for(
        recent=[{"title": "진행 중", "meeting_id": "mtg_live", "status": "analyzing"}]
    )

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=None, request="후속?")

    assert outcome.result.ok is False
    assert "분석된 회의가 없습니다" in outcome.result.summary


def test_an_unreadable_meeting_list_is_not_no_meeting(session, team) -> None:
    tools = tools_for(recent=[], recent_ok=False)

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=None, request="후속?")

    assert outcome.result.ok is False
    assert outcome.proposed == []
    assert "읽지 못했습니다" in outcome.result.summary


def test_no_question_text_reaches_the_proposal_or_the_answer(session, team) -> None:
    tools = tools_for(gaps=[gap("gap_1"), gap("gap_2", key="dependency")], questions=2)

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    assert outcome.proposed
    assert QUESTION_TEXT not in repr(outcome)
    assert not any(e.startswith("utt_") for e in outcome.proposed[0].evidence)


def test_evidence_stops_at_five() -> None:
    from autune_agent.results import ToolResult

    many = ToolResult.model_validate(_result([carried(f"gap_{n}") for n in range(5)]))
    high = ToolResult.model_validate(_result([gap(f"gap_h{n}") for n in range(5)]))
    asked = ToolResult.model_validate(_result([{"title": "질문", "id": "utt_q"}]))

    verdict = rules.decide(high, many, asked)

    assert len(verdict.evidence) == rules.MAX_EVIDENCE
    assert verdict.evidence == [f"gap_{n}" for n in range(5)]


def held(*days: str) -> list[dict[str, Any]]:
    return [
        {"title": "주간 회의", "meeting_id": f"mtg_{n}", "status": "complete", "started_at": day}
        for n, day in enumerate(days)
    ]


def test_the_date_follows_the_teams_weekly_rhythm(session, team) -> None:
    tools = tools_for(
        recurring=[carried("gap_1")],
        recent=held(
            "2026-10-02T01:00:00+00:00",
            "2026-09-25T01:00:00+00:00",
            "2026-09-18T01:00:00+00:00",
        ),
    )

    (proposal,) = invoke(
        tools, session=session, team_id=team["team"], meeting=team["meeting"]
    ).proposed

    # Friday meetings a week apart: the Friday after the latest.
    assert proposal.arguments == {"due_date": "2026-10-09", "basis": "cadence"}
    assert arguments_ok(proposal.arguments)
    assert "추천 날짜 10월 9일(금)" in proposal.rationale


def test_an_unreadable_meeting_list_still_proposes_with_the_default_date(session, team) -> None:
    tools = tools_for(recurring=[carried("gap_1")], recent_ok=False)

    (proposal,) = invoke(
        tools, session=session, team_id=team["team"], meeting=team["meeting"]
    ).proposed

    assert proposal.arguments == {"due_date": "2026-10-08", "basis": "cadence"}


def test_a_meeting_list_with_no_start_times_proposes_with_the_default_date(session, team) -> None:
    recent = [{"title": "회의", "meeting_id": "mtg_a", "status": "complete"}] * 3
    tools = tools_for(recurring=[carried("gap_1")], recent=recent)

    (proposal,) = invoke(
        tools, session=session, team_id=team["team"], meeting=team["meeting"]
    ).proposed

    assert proposal.arguments == {"due_date": "2026-10-08", "basis": "cadence"}


@pytest.mark.parametrize(
    ("held_days", "today", "expected"),
    [
        # Fewer than two meetings: three business days, over the weekend.
        ([], date(2026, 10, 9), date(2026, 10, 14)),
        ([date(2026, 10, 2)], date(2026, 10, 5), date(2026, 10, 8)),
        # Every two days: the latest plus two.
        (
            [date(2026, 10, 1), date(2026, 9, 29), date(2026, 9, 27)],
            date(2026, 10, 1),
            date(2026, 10, 5),
        ),
        # A monthly team waits at most two weeks.
        ([date(2026, 10, 1), date(2026, 9, 1)], date(2026, 10, 1), date(2026, 10, 15)),
        # Never today or earlier: the rhythm says the 6th, but it is the 7th.
        ([date(2026, 10, 5), date(2026, 10, 4)], date(2026, 10, 7), date(2026, 10, 8)),
        # A Saturday moves to Monday.
        ([date(2026, 10, 3), date(2026, 9, 26)], date(2026, 10, 5), date(2026, 10, 12)),
        # Two meetings on one day are one day.
        ([date(2026, 10, 1), date(2026, 10, 1)], date(2026, 10, 1), date(2026, 10, 6)),
    ],
)
def test_suggest_date(held_days: list[date], today: date, expected: date) -> None:
    assert rules.suggest_date(held_days, today) == expected


# --- The suggested date from M's due dates (spec sections 5 and 8, #963) ------

WEDNESDAY = date(2026, 10, 7)
"""Spec section 8's fixed day: the earliest suggestion is Thursday 10-08."""


def due(day: str, confirmed: bool = True) -> rules.Due:
    return rules.Due(date.fromisoformat(day), confirmed)


def basis_of(items: list[rules.Due]) -> str | None:
    suggestion = rules.suggest_from_due_dates(items, WEDNESDAY)
    return suggestion.basis if suggestion else None


def test_the_same_items_in_any_order_give_the_same_date() -> None:
    items = [due("2026-10-09"), due("2026-10-12", confirmed=False), due("2026-10-14")]

    suggestions = {
        rules.suggest_from_due_dates(order, WEDNESDAY)
        for order in (items, items[::-1], [items[1], items[2], items[0]])
    }

    # Three items: the third is the 80% point, Wednesday 10-14, so Thursday.
    assert suggestions == {rules.Suggestion(date(2026, 10, 15), "draft")}


def test_no_due_dates_leave_the_rhythm_to_decide() -> None:
    assert rules.suggest_from_due_dates([], WEDNESDAY) is None


@pytest.mark.parametrize(
    "items",
    [
        # All late.
        [due("2026-10-01"), due("2026-10-06")],
        # One late among later ones: work is already late, so it does not wait.
        [due("2026-10-06"), due("2026-10-13"), due("2026-10-14")],
        # Late and confirmed, beside a draft.
        [due("2026-10-05"), due("2026-10-14", confirmed=False)],
    ],
)
def test_a_confirmed_item_past_its_date_makes_it_the_next_business_day(
    items: list[rules.Due],
) -> None:
    assert rules.suggest_from_due_dates(items, WEDNESDAY) == rules.Suggestion(
        date(2026, 10, 8), "confirmed"
    )


def test_a_drafts_past_date_is_dropped_not_obeyed() -> None:
    # A date B may have misread minutes after the meeting is not late work.
    only_late_draft = [due("2026-10-06", confirmed=False)]
    beside_a_confirmed = [due("2026-10-06", confirmed=False), due("2026-10-09")]

    assert rules.suggest_from_due_dates(only_late_draft, WEDNESDAY) is None
    # Friday 10-09 is the only date left: Monday, and on confirmed dates only.
    assert rules.suggest_from_due_dates(beside_a_confirmed, WEDNESDAY) == rules.Suggestion(
        date(2026, 10, 12), "confirmed"
    )


def test_an_item_due_today_is_not_late() -> None:
    assert rules.suggest_from_due_dates([due("2026-10-07")], WEDNESDAY) == rules.Suggestion(
        date(2026, 10, 8), "confirmed"
    )


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        # Everything beyond 14 days: no date here, the rhythm decides.
        ([due("2026-10-22"), due("2026-11-30")], None),
        # Exactly on the horizon (10-21, a Wednesday) is kept.
        ([due("2026-10-21")], rules.Suggestion(date(2026, 10, 22), "confirmed")),
        # A six-week task does not pull the date out: it is dropped, not clamped.
        (
            [due("2026-10-09"), due("2026-11-30")],
            rules.Suggestion(date(2026, 10, 12), "confirmed"),
        ),
        # A draft beyond the horizon does not make the date a draft's.
        (
            [due("2026-10-09"), due("2026-11-30", confirmed=False)],
            rules.Suggestion(date(2026, 10, 12), "confirmed"),
        ),
    ],
)
def test_the_horizon_drops_long_running_work(
    items: list[rules.Due], expected: rules.Suggestion | None
) -> None:
    assert rules.suggest_from_due_dates(items, WEDNESDAY) == expected


WEEK = ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"]


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        # n = 1: the one date, a Friday, so Monday.
        (["2026-10-09"], date(2026, 10, 12)),
        # n = 2: k = ceil(1.6) = 2, the later one (Tuesday 10-13).
        (["2026-10-08", "2026-10-13"], date(2026, 10, 14)),
        # n = 5: k = 4, Tuesday 10-13.
        (WEEK, date(2026, 10, 14)),
        # n = 10, each date twice: k = 8 counts items, not dates -- Tuesday 10-13.
        (WEEK * 2, date(2026, 10, 14)),
        # n = 15: k = 12, not 13 (0.8 * 15 is not exactly 12 in floating point).
        (["2026-10-08"] * 11 + ["2026-10-09"] + ["2026-10-20"] * 3, date(2026, 10, 12)),
    ],
)
def test_the_eighty_percent_point(days: list[str], expected: date) -> None:
    suggestion = rules.suggest_from_due_dates([due(d) for d in days], WEDNESDAY)

    assert suggestion == rules.Suggestion(expected, "confirmed")


@pytest.mark.parametrize(
    ("day", "today", "expected"),
    [
        # Due on a Friday, Saturday or Sunday: the Monday after.
        ("2026-10-09", WEDNESDAY, date(2026, 10, 12)),
        ("2026-10-10", WEDNESDAY, date(2026, 10, 12)),
        ("2026-10-11", WEDNESDAY, date(2026, 10, 12)),
        # Run on a Friday or a Saturday: the earliest day is Monday.
        ("2026-10-09", date(2026, 10, 9), date(2026, 10, 12)),
        ("2026-10-10", date(2026, 10, 10), date(2026, 10, 12)),
    ],
)
def test_weekends_move_to_monday(day: str, today: date, expected: date) -> None:
    suggestion = rules.suggest_from_due_dates([due(day)], today)

    assert suggestion is not None
    assert suggestion.day == expected


def test_the_date_is_a_drafts_only_while_a_draft_is_counted() -> None:
    assert basis_of([due("2026-10-09"), due("2026-10-12")]) == "confirmed"
    assert basis_of([due("2026-10-09", confirmed=False), due("2026-10-12")]) == "draft"


def dated(*entries: tuple[str, bool]) -> list[dict[str, Any]]:
    return [{"date": day, "confirmed": confirmed} for day, confirmed in entries]


def test_the_proposal_follows_the_due_dates_and_skips_the_meeting_list(session, team) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    tools = tools_for(
        recurring=[carried("gap_1")],
        due=dated(("2026-10-07", True), ("2026-10-08", False)),
        calls=calls,
    )

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    (proposal,) = outcome.proposed
    # Monday 10-05: two items, the later is Thursday 10-08, so Friday.
    assert proposal.arguments == {"due_date": "2026-10-09", "basis": "draft"}
    assert arguments_ok(proposal.arguments)
    assert "10월 9일(금), 초안 기준" in outcome.result.summary
    assert RECENT not in names(calls)


def test_confirmed_due_dates_say_so(session, team) -> None:
    tools = tools_for(recurring=[carried("gap_1")], due=dated(("2026-10-07", True)))

    (proposal,) = invoke(
        tools, session=session, team_id=team["team"], meeting=team["meeting"]
    ).proposed

    assert proposal.arguments == {"due_date": "2026-10-08", "basis": "confirmed"}
    assert "확정 기한 기준" in proposal.rationale


@pytest.mark.parametrize(
    "setup",
    [
        # B has not shipped the read: the toolbox answers "not available".
        {"with_due": False},
        # B's read failed.
        {"due": dated(("2026-10-07", True)), "due_ok": False},
        # Nothing dated.
        {"due": []},
        # Entries that are not a date and a flag are skipped, not guessed at.
        {"due": [{"date": "다음 주", "confirmed": True}, {"date": "2026-10-07"}, "2026-10-07"]},
    ],
)
def test_without_usable_due_dates_the_rhythm_decides(session, team, setup: dict[str, Any]) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    tools = tools_for(recurring=[carried("gap_1")], calls=calls, **setup)

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    (proposal,) = outcome.proposed
    # No meeting days either: three business days from Monday.
    assert proposal.arguments == {"due_date": "2026-10-08", "basis": "cadence"}
    assert outcome.result.ok is True
    assert "회의 주기 기준" in outcome.result.summary
    assert names(calls)[-1] == RECENT


def test_nothing_about_an_owner_reaches_the_proposal_or_the_answer(session, team) -> None:
    # Were B's row ever to carry more, only the date and the flag are read.
    entry = {"date": "2026-10-07", "confirmed": True, "assignee": "박지영", "title": "결제 QA"}
    tools = tools_for(recurring=[carried("gap_1")], due=[entry])

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    assert outcome.proposed
    assert "박지영" not in repr(outcome)
    assert "결제 QA" not in repr(outcome)


# --- Public holidays (#964) -----------------------------------------------------------

CHUSEOK_2026 = {date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26)}
"""Thursday to Saturday. Then 개천절 on Saturday 10-03 gives Monday 10-05 off,
and 한글날 is Friday 10-09."""
AUTUMN_2026 = frozenset(CHUSEOK_2026 | {date(2026, 10, 3), date(2026, 10, 5), date(2026, 10, 9)})


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 9, 23), True),
        (date(2026, 9, 24), False),  # 추석 연휴
        (date(2026, 9, 27), False),  # a Sunday
        (date(2026, 10, 5), False),  # 개천절's substitute day
        (date(2026, 10, 6), True),
    ],
)
def test_a_business_day_is_a_weekday_that_is_not_a_holiday(day: date, expected: bool) -> None:
    assert rules.is_business_day(day, AUTUMN_2026) is expected


def test_the_earliest_day_skips_chuseok() -> None:
    # Wednesday before 추석: Thursday to Sunday are off, so Monday 9-28.
    due = [rules.Due(date(2026, 9, 21), confirmed=True)]

    assert rules.suggest_from_due_dates(due, date(2026, 9, 23), AUTUMN_2026) == (
        rules.Suggestion(date(2026, 9, 28), "confirmed")
    )


def test_the_day_after_the_due_point_skips_a_substitute_holiday() -> None:
    # Due Friday 10-02; Monday 10-05 is 개천절's substitute day, so Tuesday.
    due = [rules.Due(date(2026, 10, 2), confirmed=True)]

    without = rules.suggest_from_due_dates(due, date(2026, 9, 30))
    with_off = rules.suggest_from_due_dates(due, date(2026, 9, 30), AUTUMN_2026)

    assert without == rules.Suggestion(date(2026, 10, 5), "confirmed")
    assert with_off == rules.Suggestion(date(2026, 10, 6), "confirmed")


def test_the_rhythm_moves_off_a_holiday() -> None:
    # Friday meetings a week apart land on 한글날, Friday 10-09: Monday 10-12.
    held_days = [date(2026, 10, 2), date(2026, 9, 18)]

    assert rules.suggest_date(held_days, date(2026, 10, 2)) == date(2026, 10, 16)
    assert rules.suggest_date(held_days[:1] + [date(2026, 9, 25)], date(2026, 10, 2)) == date(
        2026, 10, 9
    )
    assert rules.suggest_date(
        held_days[:1] + [date(2026, 9, 25)], date(2026, 10, 2), AUTUMN_2026
    ) == date(2026, 10, 12)


def test_three_business_days_count_no_holiday() -> None:
    # Wednesday 9-23 before 추석: 9-28, 9-29, 9-30.
    assert rules.suggest_date([], date(2026, 9, 23), AUTUMN_2026) == date(2026, 9, 30)


def test_the_proposal_reads_the_holidays_and_skips_them(session, team) -> None:
    spans: list[tuple[str, str]] = []
    tools = tools_for(
        recurring=[carried("gap_1")],
        due=dated(("2026-10-08", True)),
        off=["2026-10-09"],
        holiday_spans=spans,
    )

    (proposal,) = invoke(
        tools, session=session, team_id=team["team"], meeting=team["meeting"]
    ).proposed

    # Due Thursday 10-08; Friday 10-09 is 한글날, so Monday 10-12.
    assert proposal.arguments == {"due_date": "2026-10-12", "basis": "confirmed"}
    # From the run's day (Monday 10-05) to 45 days on.
    assert spans == [("2026-10-05", "2026-11-19")]


@pytest.mark.parametrize(
    "setup",
    [
        # B has not shipped the read.
        {"with_holidays": False},
        # B's read failed.
        {"off": ["2026-10-09"], "off_ok": False},
        # Entries that are not ISO dates are skipped.
        {"off": ["한글날", 20261009, None]},
    ],
)
def test_without_holidays_only_weekends_are_skipped(session, team, setup: dict[str, Any]) -> None:
    tools = tools_for(recurring=[carried("gap_1")], due=dated(("2026-10-08", True)), **setup)

    outcome = invoke(tools, session=session, team_id=team["team"], meeting=team["meeting"])

    (proposal,) = outcome.proposed
    # The proposal still goes out; Friday 10-09 counts as a business day.
    assert proposal.arguments == {"due_date": "2026-10-09", "basis": "confirmed"}
