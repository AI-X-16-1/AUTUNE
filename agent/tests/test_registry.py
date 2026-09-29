"""The registry and the return contract (agent-layer.md section 4)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from autune_agent.main import (
    BudgetExceededError,
    CallBudget,
    Tool,
    Toolbox,
    ToolContractError,
    collect_tools,
)
from autune_agent.results import MAX_ITEMS, ToolResult
from autune_agent.testing import mock_tool

SESSION: Any = object()
"""No tool here touches a database; the toolbox only passes the session through."""


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": True,
        "reason": None,
        "summary": "두 건.",
        "items": [{"title": "a"}, {"title": "b"}],
        "evidence": ["utt_1a2b"],
        "confidence": 0.8,
        "truncated": False,
    }
    return base | overrides


def test_collects_module_b_tools_by_iterating_the_module_list() -> None:
    tools = collect_tools()

    assert "extraction.open_action_items" in tools
    assert all(
        name.split(".")[0] in {"audio", "extraction", "gap", "context", "intelligence"}
        for name in tools
    )
    assert tools["extraction.open_action_items"].description.startswith("Use this")


def test_a_module_without_tools_py_contributes_nothing() -> None:
    assert collect_tools(["audio"]) == {}


def test_more_than_five_items_is_cut_to_five_and_marked_truncated() -> None:
    result = ToolResult.model_validate(_payload(items=[{"title": str(i)} for i in range(9)]))

    assert len(result.items) == MAX_ITEMS
    assert result.truncated is True


def test_evidence_that_is_not_an_id_is_refused() -> None:
    with pytest.raises(ValidationError):
        ToolResult.model_validate(_payload(evidence=["김 팀장이 다음 주까지 올린다고 했습니다"]))


def test_a_tool_that_breaks_the_contract_raises_rather_than_routes() -> None:
    tool = Tool(name="gap.broken", description="Use this never.", fn=lambda _s: {"rows": []})

    with pytest.raises(ToolContractError):
        tool(SESSION)


def test_a_subagent_sees_only_the_tools_it_named() -> None:
    tools = {
        "extraction.open_action_items": mock_tool("extraction.open_action_items", _payload()),
        "intelligence.speaking_ratio": mock_tool("intelligence.speaking_ratio", _payload()),
    }
    box = Toolbox(tools, SESSION, CallBudget(), allowed=["extraction.open_action_items"])

    assert list(box.describe()) == ["extraction.open_action_items"]
    refused = box.call("intelligence.speaking_ratio")
    assert refused.ok is False
    assert refused.reason == "intelligence.speaking_ratio is not available here"


def test_a_named_tool_nobody_has_shipped_yet_is_not_an_error() -> None:
    box = Toolbox({}, SESSION, CallBudget(), allowed=["gap.unresolved_topics"])

    assert box.describe() == {}


def test_the_budget_is_shared_and_stops_the_run() -> None:
    tools = {"extraction.x": mock_tool("extraction.x", _payload())}
    budget = CallBudget(limit=2)
    first = Toolbox(tools, SESSION, budget)
    second = Toolbox(tools, SESSION, budget)

    first.call("extraction.x")
    second.call("extraction.x")
    with pytest.raises(BudgetExceededError):
        first.call("extraction.x")
