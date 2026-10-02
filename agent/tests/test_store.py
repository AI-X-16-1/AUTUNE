"""Every run leaves a row, and the row keeps no text a meeting deletion would miss."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from autune_agent.main import CallBudget, Subagent, SubagentState, Toolbox
from autune_agent.main.store import BUDGET_ANSWER, run_and_record
from autune_agent.main.toolcall import FunctionCall
from autune_agent.results import ProposedAction, SubagentResult, ToolResult
from autune_agent.testing import FakeRouter, ScriptedToolModel, example_subagent, mock_tool

OPEN_ITEMS = {
    "ok": True,
    "summary": "김 팀장 담당 API 문서가 내일 마감입니다.",
    "items": [{"title": "API 문서 올리기"}],
    "evidence": ["utt_aa11"],
}
TOOLS = {"extraction.open_action_items": mock_tool("extraction.open_action_items", OPEN_ITEMS)}
WORKLOAD = {"workload": example_subagent("workload", ("extraction.open_action_items",))}


def _proposing_subagent() -> Subagent:
    def build(toolbox: Toolbox) -> Any:
        def act(state: SubagentState) -> SubagentState:
            toolbox.call("extraction.open_action_items")
            action = ProposedAction(
                kind="dm",
                title="김 팀장에게 마감 알림",
                tool="slack.dm",
                level="L2",
                rationale="마감 하루 전",
                evidence=["utt_aa11"],
            )
            result = ToolResult(ok=True, summary="알림을 제안합니다.")
            return {"outcome": SubagentResult(result=result, proposed=[action])}

        graph = StateGraph(SubagentState)
        graph.add_node("act", act)
        graph.add_edge(START, "act")
        graph.add_edge("act", END)
        return graph.compile()

    return Subagent(
        name="workload",
        description="Use this in tests.",
        tools=("extraction.open_action_items",),
        build=build,
    )


def test_an_answered_chat_run_is_recorded_with_its_trace(
    session: Session, team: dict[str, str]
) -> None:
    row, state = run_and_record(
        "업무 몰린 사람?",
        session=session,
        router=FakeRouter({"업무": "workload"}),
        team_id=team["team"],
        requested_by=team["member"],
        trigger={"kind": "chat"},
        subagents=WORKLOAD,
        tools=TOOLS,
    )

    assert row.outcome == "answered"
    assert row.route == "workload"
    assert row.steps == [
        {
            "tool": "extraction.open_action_items",
            "ok": True,
            "evidence": ["utt_aa11"],
            "truncated": False,
        }
    ]
    assert state["answer"] == OPEN_ITEMS["summary"]


def test_a_run_about_no_meeting_keeps_no_text(session: Session, team: dict[str, str]) -> None:
    subagents = {"workload": _proposing_subagent()}

    row, state = run_and_record(
        "업무",
        session=session,
        router=FakeRouter({"업무": "workload"}),
        team_id=team["team"],
        trigger={"kind": "chat"},
        subagents=subagents,
        tools=TOOLS,
    )

    assert state["answer"]  # the person who asked still gets it
    assert row.answer is None
    assert row.proposed == [
        {"kind": "dm", "tool": "slack.dm", "level": "L2", "evidence": ["utt_aa11"]}
    ]
    stored = repr([row.steps, row.proposed, row.trigger])
    assert "김 팀장" not in stored


def test_a_run_about_a_meeting_keeps_no_text_either(session: Session, team: dict[str, str]) -> None:
    """Its answer may quote another meeting of the team, which the cascade from
    this one would not delete (#449 review)."""
    row, _ = run_and_record(
        "업무",
        session=session,
        router=FakeRouter({"업무": "workload"}),
        team_id=team["team"],
        meeting_id=team["meeting"],
        trigger={"kind": "event", "event": "autune.intelligence.completed"},
        subagents={"workload": _proposing_subagent()},
        tools=TOOLS,
    )

    assert row.answer is None
    assert "title" not in row.proposed[0]
    assert "김 팀장" not in repr([row.steps, row.proposed, row.actions])


def test_the_budget_stops_the_run_and_keeps_the_trace(
    session: Session, team: dict[str, str]
) -> None:
    def greedy(toolbox: Toolbox) -> Any:
        def loop(state: SubagentState) -> SubagentState:
            while True:
                toolbox.call("extraction.open_action_items")

        graph = StateGraph(SubagentState)
        graph.add_node("loop", loop)
        graph.add_edge(START, "loop")
        graph.add_edge("loop", END)
        return graph.compile()

    subagents = {
        "workload": Subagent(
            name="workload",
            description="Use this in tests.",
            tools=("extraction.open_action_items",),
            build=greedy,
        )
    }
    row, state = run_and_record(
        "업무",
        session=session,
        router=FakeRouter({"업무": "workload"}),
        team_id=team["team"],
        trigger={"kind": "chat"},
        subagents=subagents,
        tools=TOOLS,
        budget=CallBudget(limit=3),
    )

    assert row.outcome == "budget_exceeded"
    assert len(row.steps) == 3
    assert state["answer"] == BUDGET_ANSWER


def test_a_bug_is_recorded_and_still_raised(session: Session, team: dict[str, str]) -> None:
    broken = {"extraction.open_action_items": mock_tool("extraction.open_action_items", {})}

    with pytest.raises(Exception, match="broke the return contract"):
        run_and_record(
            "업무",
            session=session,
            router=FakeRouter({"업무": "workload"}),
            team_id=team["team"],
            trigger={"kind": "chat"},
            subagents=WORKLOAD,
            tools=broken,
        )

    from autune_agent.models import AgentRun

    assert [r.outcome for r in session.query(AgentRun)] == ["failed"]


def test_an_asked_turn_records_tools_and_no_text(session: Session, team: dict[str, str]) -> None:
    tools = {
        "extraction.open_action_items": mock_tool(
            "extraction.open_action_items",
            {"ok": True, "summary": "1건", "items": [{"title": "문서"}], "evidence": ["act_1"]},
        )
    }
    row, _ = run_and_record(
        "기한?",
        session=session,
        router=FakeRouter(),
        team_id=team["team"],
        trigger={"kind": "chat"},
        subagents={},
        tools=tools,
        asker=ScriptedToolModel([[FunctionCall("extraction__open_action_items", {})], "DONE"]),
    )

    assert row.route == "ask" and row.outcome == "answered"
    assert row.steps == [
        {
            "tool": "extraction.open_action_items",
            "ok": True,
            "evidence": ["act_1"],
            "truncated": False,
        }
    ]
    assert row.answer is None
