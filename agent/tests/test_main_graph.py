"""The main agent routes, delegates and answers (agent-layer.md section 3)."""

from __future__ import annotations

from typing import Any

import pytest

from autune_agent.main import SUBAGENT_NAMES, CallBudget, Subagent, collect_subagents, run
from autune_agent.testing import FakeRouter, example_subagent, mock_tool

SESSION: Any = object()

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
        "업무가 몰린 사람 있어?", session=SESSION, router=router, subagents=subagents, tools=TOOLS
    )

    assert state["route"] == "workload"
    assert state["outcome"].result.ok is True
    assert [i.title for i in state["outcome"].result.items] == ["API 문서 올리기", "QA 일정"]
    assert state["answer"] == "마감이 가까운 액션아이템 2건."
    assert router.seen == [{"workload": "Use this in tests. Example subagent workload."}]


def test_no_fitting_subagent_is_an_answer_not_a_crash() -> None:
    subagents = {"workload": example_subagent("workload", ("extraction.open_action_items",))}

    state = run(
        "점심 뭐 먹지", session=SESSION, router=FakeRouter(), subagents=subagents, tools=TOOLS
    )

    assert state["route"] is None
    assert state["outcome"].result.ok is False
    assert state["outcome"].result.reason == "no subagent fits this request"


def test_a_route_to_an_unknown_subagent_is_treated_as_no_route() -> None:
    state = run(
        "업무", session=SESSION, router=FakeRouter({"업무": "payroll"}), subagents={}, tools=TOOLS
    )

    assert state["route"] is None


def test_a_delegation_spends_the_same_budget() -> None:
    budget = CallBudget()
    subagents = {"report": example_subagent("report", ("extraction.open_action_items",))}

    run(
        "리포트",
        session=SESSION,
        router=FakeRouter({"리포트": "report"}),
        subagents=subagents,
        tools=TOOLS,
        budget=budget,
    )

    assert budget.used == 1


def test_no_subagent_is_built_yet_so_none_is_collected() -> None:
    # Each owner's package exists and exports nothing until they build it.
    assert collect_subagents() == {}
    assert SUBAGENT_NAMES == ("research", "briefing", "followup", "workload", "report")


def test_a_subagent_cannot_list_a_speaking_ratio_tool() -> None:
    with pytest.raises(ValueError, match="personal-only"):
        Subagent(
            name="workload",
            description="Use this never.",
            tools=("intelligence.speaking_ratio",),
            build=lambda _box: None,  # type: ignore[arg-type, return-value]
        )
