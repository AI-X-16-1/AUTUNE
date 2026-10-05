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
    calls: list[tuple[str, dict[str, Any]]] | None = None,
) -> dict[str, Tool]:
    log = [] if calls is None else calls

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
    assert set(SUBAGENT.tools) == {OPEN_GAPS, RECURRING, QUESTIONS, RECENT, OPEN_ITEM}


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
    assert (proposal.level, proposal.tool, proposal.kind) == ("L2", WRITE, "followup_meeting")
    # The trigger's meeting is the run's: the action is bound to it when it runs.
    # No meeting days to read, so three business days from Monday.
    assert proposal.arguments == {"due_date": "2026-10-08"}
    assert proposal.evidence == ["gap_now"]
    assert "다시 열린 항목 1개" in outcome.result.summary
    assert "10월 8일(목)" in outcome.result.summary
    assert names(calls) == [OPEN_GAPS, RECURRING, QUESTIONS, OPEN_ITEM, RECENT]
    assert {args["meeting_id"] for _, args in calls if args} == {team["meeting"]}


def test_the_write_is_one_b_declares_l2_and_takes_the_meeting_and_a_date() -> None:
    """A proposal cannot demote a write, but an L1 one would run with no lead at all.
    And an argument the write does not take fails the approval (``bind_scope``)."""
    write = collect_actions(["extraction"])[WRITE]
    assert write.level == "L2"
    assert write.parameters == {"team_id", "meeting_id", "due_date"}


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
    assert proposal.arguments == {"meeting_id": team["meeting"], "due_date": "2026-10-08"}
    assert arguments_ok(proposal.arguments)
    # The first open_gaps is refused by the scope (no meeting) before it reaches C.
    # The meeting list it read to pick M also gives the date: no second read.
    assert names(calls) == [RECENT, OPEN_GAPS, RECURRING, QUESTIONS, OPEN_ITEM]
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
    assert proposal.arguments == {"due_date": "2026-10-09"}
    assert arguments_ok(proposal.arguments)
    assert "추천 날짜 10월 9일(금)" in proposal.rationale


def test_an_unreadable_meeting_list_still_proposes_with_the_default_date(session, team) -> None:
    tools = tools_for(recurring=[carried("gap_1")], recent_ok=False)

    (proposal,) = invoke(
        tools, session=session, team_id=team["team"], meeting=team["meeting"]
    ).proposed

    assert proposal.arguments == {"due_date": "2026-10-08"}


def test_a_meeting_list_with_no_start_times_proposes_with_the_default_date(session, team) -> None:
    recent = [{"title": "회의", "meeting_id": "mtg_a", "status": "complete"}] * 3
    tools = tools_for(recurring=[carried("gap_1")], recent=recent)

    (proposal,) = invoke(
        tools, session=session, team_id=team["team"], meeting=team["meeting"]
    ).proposed

    assert proposal.arguments == {"due_date": "2026-10-08"}


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
