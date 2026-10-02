"""The registry and the return contract (agent-layer.md section 4)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from autune_agent.main import (
    BudgetExceededError,
    CallBudget,
    RunScope,
    Tool,
    Toolbox,
    ToolContractError,
    collect_tools,
    refuse_tracing,
)
from autune_agent.results import MAX_ITEMS, ToolResult
from autune_agent.testing import mock_tool
from autune_core import Meeting, Team

SESSION: Any = object()
"""No tool here touches a database; the toolbox only passes the session through."""

SCOPE = RunScope(team_id="team_a")


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


def test_a_module_without_tools_py_contributes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # A package with no tools submodule. Not a real module: which of the five
    # has shipped its tools.py changes as they land.
    monkeypatch.setitem(sys.modules, "autune_fake", types.ModuleType("autune_fake"))

    assert collect_tools(["fake"]) == {}


def test_module_a_tools_are_collected() -> None:
    assert sorted(collect_tools(["audio"])) == [
        "audio.find_utterances",
        "audio.meeting_overview",
        "audio.quote_utterances",
        "audio.recent_meetings",
        "audio.search_team_meetings",
    ]


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
    box = Toolbox(
        tools, SESSION, CallBudget(), allowed=["extraction.open_action_items"], scope=SCOPE
    )

    assert list(box.describe()) == ["extraction.open_action_items"]
    refused = box.call("intelligence.speaking_ratio")
    assert refused.ok is False
    assert refused.reason == "intelligence.speaking_ratio is not available here"


def test_a_named_tool_nobody_has_shipped_yet_is_not_an_error() -> None:
    box = Toolbox({}, SESSION, CallBudget(), allowed=["gap.unresolved_topics"], scope=SCOPE)

    assert box.describe() == {}


def test_the_budget_is_shared_and_stops_the_run() -> None:
    tools = {"extraction.x": mock_tool("extraction.x", _payload())}
    budget = CallBudget(limit=2)
    first = Toolbox(tools, SESSION, budget, allowed=tools, scope=SCOPE)
    second = Toolbox(tools, SESSION, budget, allowed=tools, scope=SCOPE)

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


def test_a_refused_call_still_spends_the_budget() -> None:
    budget = CallBudget(limit=1)
    box = Toolbox({}, SESSION, budget, allowed=[], scope=SCOPE)

    box.call("gap.anything")
    with pytest.raises(BudgetExceededError):
        box.call("gap.anything")


def _scoped_tool(name: str, seen: list[dict[str, Any]]) -> Tool:
    """A tool shaped like module B's: its range comes in as arguments."""

    def fn(session: Any, team_id: str, meeting_id: str | None = None) -> dict[str, Any]:
        seen.append({"team_id": team_id, "meeting_id": meeting_id})
        return _payload()

    return Tool(name=name, description="Use this in tests.", fn=fn)


def test_the_run_writes_the_team_id_the_model_left_out() -> None:
    seen: list[dict[str, Any]] = []
    tools = {"extraction.x": _scoped_tool("extraction.x", seen)}
    box = Toolbox(tools, SESSION, CallBudget(), allowed=tools, scope=SCOPE)

    assert box.call("extraction.x").ok is True
    assert seen == [{"team_id": "team_a", "meeting_id": None}]


def test_a_team_id_the_run_was_not_started_for_is_refused() -> None:
    seen: list[dict[str, Any]] = []
    tools = {"extraction.x": _scoped_tool("extraction.x", seen)}
    budget = CallBudget()
    box = Toolbox(tools, SESSION, budget, allowed=tools, scope=SCOPE)

    refused = box.call("extraction.x", team_id="team_b")

    assert refused.ok is False
    assert refused.reason == "team_id is outside this run's team"
    assert seen == []
    assert budget.steps == [
        {"tool": "extraction.x", "ok": False, "evidence": [], "truncated": False}
    ]


def test_another_teams_meeting_reads_as_missing(session: Session, team: dict[str, str]) -> None:
    other = Team(name="다른 팀")
    session.add(other)
    session.flush()
    theirs = Meeting(team_id=other.id, title="남의 회의")
    session.add(theirs)
    session.commit()
    seen: list[dict[str, Any]] = []
    tools = {"extraction.x": _scoped_tool("extraction.x", seen)}
    box = Toolbox(tools, session, CallBudget(), allowed=tools, scope=RunScope(team_id=team["team"]))

    refused = box.call("extraction.x", meeting_id=theirs.id)
    missing = box.call("extraction.x", meeting_id="mtg_nobody")
    own = box.call("extraction.x", meeting_id=team["meeting"])

    assert refused.ok is False and missing.ok is False
    assert refused.reason == missing.reason == "meeting not found"
    assert own.ok is True
    assert seen == [{"team_id": team["team"], "meeting_id": team["meeting"]}]


def test_a_run_about_a_meeting_fills_it_in(session: Session, team: dict[str, str]) -> None:
    seen: list[dict[str, Any]] = []
    tools = {"extraction.x": _scoped_tool("extraction.x", seen)}
    scope = RunScope(team_id=team["team"], meeting_id=team["meeting"])
    box = Toolbox(tools, session, CallBudget(), allowed=tools, scope=scope)

    box.call("extraction.x")

    assert seen == [{"team_id": team["team"], "meeting_id": team["meeting"]}]


def test_toolbox_has_no_unscoped_default() -> None:
    with pytest.raises(TypeError):
        Toolbox({}, SESSION, CallBudget(), allowed=[])  # type: ignore[call-arg]


def _per_meeting(seen: list[str]) -> Tool:
    """Shaped like B's ``meeting_action_items(session, meeting_id)``."""

    def fn(session: Any, meeting_id: str) -> dict[str, Any]:
        seen.append(meeting_id)
        return _payload()

    return Tool(name="extraction.per_meeting", description="Use this in tests.", fn=fn)


def test_a_per_meeting_tool_in_a_run_about_no_meeting_is_a_route_not_a_crash() -> None:
    seen: list[str] = []
    tools = {"extraction.per_meeting": _per_meeting(seen)}
    box = Toolbox(tools, SESSION, CallBudget(), allowed=tools, scope=SCOPE)

    result = box.call("extraction.per_meeting")

    assert result.ok is False
    assert result.reason == "this run is about no meeting; pass meeting_id"
    assert seen == []


def test_an_argument_the_tool_does_not_take_is_refused_without_its_name() -> None:
    seen: list[dict[str, Any]] = []
    tools = {"extraction.x": _scoped_tool("extraction.x", seen)}
    box = Toolbox(tools, SESSION, CallBudget(), allowed=tools, scope=SCOPE)

    result = box.call("extraction.x", 김팀장_전화="010")

    assert result.ok is False
    assert result.reason == "unexpected argument"
    assert seen == []


def test_a_missing_argument_other_than_the_meeting_is_named_from_the_code() -> None:
    def fn(session: Any, team_id: str, query: str) -> dict[str, Any]:
        return _payload()

    tools = {"extraction.search": Tool(name="extraction.search", description="Use this.", fn=fn)}
    box = Toolbox(tools, SESSION, CallBudget(), allowed=tools, scope=SCOPE)

    assert box.call("extraction.search").reason == "missing argument: query"


def _person_tool(seen: list[str]) -> Tool:
    def person_action_items(session: Any, team_id: str, user_id: str) -> dict[str, Any]:
        seen.append(user_id)
        return _payload()

    return Tool(
        name="extraction.person_action_items", description="Use this.", fn=person_action_items
    )


def test_a_read_without_a_user_id_is_about_the_person_asking() -> None:
    # "내 기한 지난 거" -- the model cannot know the asker's id; the run does.
    seen: list[str] = []
    tools = {"extraction.person_action_items": _person_tool(seen)}
    scope = RunScope(team_id="team_a", user_id="user_me")
    box = Toolbox(tools, SESSION, CallBudget(), allowed=tools, scope=scope)

    assert box.call("extraction.person_action_items").ok is True
    assert box.call("extraction.person_action_items", user_id="user_other").ok is True
    assert seen == ["user_me", "user_other"]


def test_a_run_nobody_asked_for_fills_in_no_user() -> None:
    seen: list[str] = []
    tools = {"extraction.person_action_items": _person_tool(seen)}
    box = Toolbox(tools, SESSION, CallBudget(), allowed=tools, scope=SCOPE)

    result = box.call("extraction.person_action_items")

    assert result.ok is False and result.reason == "missing argument: user_id"
    assert seen == []
