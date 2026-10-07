"""The main agent routes, delegates and answers (agent-layer.md section 3)."""

from __future__ import annotations

from typing import Any

import pytest

from autune_agent.main import (
    SUBAGENT_NAMES,
    CallBudget,
    RunScope,
    Subagent,
    collect_subagents,
    run,
)
from autune_agent.main.graph import LOOKUP_MARK
from autune_agent.main.toolcall import FunctionCall
from autune_agent.testing import FakeRouter, ScriptedToolModel, example_subagent, mock_tool

SESSION: Any = object()
SCOPE = RunScope(team_id="team_a")

OPEN_ITEMS = {
    "ok": True,
    "summary": "마감이 가까운 액션아이템 2건.",
    "items": [{"title": "API 문서 올리기", "score": 0.8}, {"title": "QA 일정", "score": 0.5}],
    "evidence": ["utt_aa11", "utt_bb22"],
}
TOOLS = {"extraction.open_action_items": mock_tool("extraction.open_action_items", OPEN_ITEMS)}


def test_a_request_goes_to_the_subagent_the_router_picks() -> None:
    router = FakeRouter({"업무": "workload"})
    subagents = {"workload": example_subagent("workload", ("extraction.open_action_items",))}

    state = run(
        "업무가 몰린 사람 있어?",
        session=SESSION,
        scope=SCOPE,
        router=router,
        subagents=subagents,
        tools=TOOLS,
    )

    assert state["route"] == "workload"
    assert state["outcome"].result.ok is True
    assert [i.title for i in state["outcome"].result.items] == ["API 문서 올리기", "QA 일정"]
    assert state["answer"] == "마감이 가까운 액션아이템 2건."
    assert router.seen == [{"workload": "Use this in tests. Example subagent workload."}]


def test_no_fitting_subagent_is_an_answer_not_a_crash() -> None:
    subagents = {"workload": example_subagent("workload", ("extraction.open_action_items",))}

    state = run(
        "점심 뭐 먹지",
        session=SESSION,
        scope=SCOPE,
        router=FakeRouter(),
        subagents=subagents,
        tools=TOOLS,
    )

    assert state["route"] is None
    assert state["outcome"].result.ok is False
    assert state["outcome"].result.reason == "no subagent fits this request"


def test_a_route_to_an_unknown_subagent_is_treated_as_no_route() -> None:
    state = run(
        "업무",
        session=SESSION,
        scope=SCOPE,
        router=FakeRouter({"업무": "payroll"}),
        subagents={},
        tools=TOOLS,
    )

    assert state["route"] is None


def test_a_delegation_spends_the_same_budget() -> None:
    budget = CallBudget()
    subagents = {"report": example_subagent("report", ("extraction.open_action_items",))}

    run(
        "리포트",
        session=SESSION,
        scope=SCOPE,
        router=FakeRouter({"리포트": "report"}),
        subagents=subagents,
        tools=TOOLS,
        budget=budget,
    )

    assert budget.used == 1


def test_only_built_subagents_are_collected() -> None:
    # Each owner's package exists and exports nothing until they build it.
    assert set(collect_subagents()) <= set(SUBAGENT_NAMES)
    assert SUBAGENT_NAMES == ("research", "briefing", "followup", "workload", "report", "tracker")


@pytest.mark.parametrize(
    "tool", ["intelligence.speaking_ratio", "intelligence.SpeakingRatio", "e.my-speaking-ratio"]
)
def test_a_subagent_cannot_list_a_speaking_ratio_tool(tool: str) -> None:
    with pytest.raises(ValueError, match="personal-only"):
        Subagent(
            name="workload",
            description="Use this never.",
            tools=(tool,),
            build=lambda _box: None,  # type: ignore[arg-type, return-value]
        )


def test_a_turn_no_subagent_fits_is_asked_when_a_model_is_given() -> None:
    tools = {"extraction.open_action_items": mock_tool("extraction.open_action_items", OPEN_ITEMS)}
    model = ScriptedToolModel([[FunctionCall("extraction__open_action_items", {})], "DONE"])

    state = run(
        "기한 지난 거 있어?",
        session=SESSION,
        scope=SCOPE,
        router=FakeRouter(),
        subagents={},
        tools=tools,
        asker=model,
    )

    assert state["route"] == "ask"
    assert state["outcome"].result.ok is True
    assert state["outcome"].proposed == []
    assert state["answer"] == "마감이 가까운 액션아이템 2건."


def test_without_a_model_an_unfit_turn_is_still_unrouted() -> None:
    state = run(
        "점심?", session=SESSION, scope=SCOPE, router=FakeRouter(), subagents={}, tools=TOOLS
    )

    assert state["route"] is None


def test_a_triggered_run_never_asks() -> None:
    model = ScriptedToolModel(["DONE"])

    state = run(
        "autune.intelligence.completed",
        session=SESSION,
        scope=SCOPE,
        router=FakeRouter(),
        subagents={},
        tools=TOOLS,
        asker=model,
        route_to="report",
    )

    assert model.sent == []
    assert state["route"] is None


def test_a_subagent_that_answers_questions_is_marked_for_the_router() -> None:
    """#879: a lookup reaches a subagent only when it declared it answers them."""
    router = FakeRouter()
    plain = example_subagent("workload", ())
    asked = example_subagent("report", ())
    asked = Subagent(
        name=asked.name,
        description=asked.description,
        tools=asked.tools,
        build=asked.build,
        answers_lookups=True,
    )

    run(
        "결정 밀도가 뭐야?",
        session=SESSION,
        scope=SCOPE,
        router=router,
        subagents={"workload": plain, "report": asked},
        tools={},
    )

    assert router.seen == [
        {
            "workload": "Use this in tests. Example subagent workload.",
            "report": f"Use this in tests. Example subagent report.{LOOKUP_MARK}",
        }
    ]


def test_a_subagent_answers_no_lookups_unless_it_says_so() -> None:
    assert example_subagent("research", ()).answers_lookups is False


def test_the_report_subagent_answers_questions_about_module_e() -> None:
    assert collect_subagents()["report"].answers_lookups is True
