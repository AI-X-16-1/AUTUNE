"""The registry and the return contract (agent-layer.md section 4)."""

from __future__ import annotations

import sys
import types
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
    refuse_tracing,
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
    first = Toolbox(tools, SESSION, budget, allowed=tools)
    second = Toolbox(tools, SESSION, budget, allowed=tools)

    first.call("extraction.x")
    second.call("extraction.x")
    with pytest.raises(BudgetExceededError):
        first.call("extraction.x")


def test_evidence_with_a_trailing_newline_is_refused() -> None:
    with pytest.raises(ValidationError):
        ToolResult.model_validate(_payload(evidence=["utt_ab\n"]))


def test_a_refused_value_stays_out_of_the_error_message() -> None:
    sentence = "김 팀장 010-1234-5678"
    with pytest.raises(ValidationError) as caught:
        ToolResult.model_validate(_payload(evidence=[sentence]))

    assert sentence not in str(caught.value)


def test_toolbox_has_no_everything_default() -> None:
    with pytest.raises(TypeError):
        Toolbox({}, SESSION, CallBudget())  # type: ignore[call-arg]


def _fake_tools_module(monkeypatch: pytest.MonkeyPatch, **attrs: Any) -> None:
    package = types.ModuleType("autune_fake")
    module = types.ModuleType("autune_fake.tools")
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, "autune_fake", package)
    monkeypatch.setitem(sys.modules, "autune_fake.tools", module)


def my_speaking_ratio(_session: Any) -> dict[str, Any]:
    """Use this for the subject only."""
    return _payload()


def team_items(_session: Any) -> dict[str, Any]:
    """Use this for the team's open items."""
    return _payload()


def test_a_declared_personal_only_tool_is_never_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_tools_module(
        monkeypatch, TOOLS=[my_speaking_ratio, team_items], PERSONAL_ONLY_TOOLS=[my_speaking_ratio]
    )

    assert list(collect_tools(["fake"])) == ["fake.team_items"]


def test_an_undeclared_tool_that_looks_personal_only_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_tools_module(monkeypatch, TOOLS=[my_speaking_ratio])

    with pytest.raises(ToolContractError, match="PERSONAL_ONLY_TOOLS"):
        collect_tools(["fake"])


@pytest.mark.parametrize("value", ["true", "1", "True"])
def test_the_graph_refuses_to_run_with_tracing_on(value: str) -> None:
    with pytest.raises(RuntimeError, match="LANGSMITH_TRACING"):
        refuse_tracing({"LANGSMITH_TRACING": value})


def test_tracing_off_or_unset_is_fine() -> None:
    refuse_tracing({})
    refuse_tracing({"LANGCHAIN_TRACING_V2": "false"})
