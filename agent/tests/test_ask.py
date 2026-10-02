"""The ask loop: which tools a turn may use, how they are declared, and the rounds."""

from __future__ import annotations

from typing import Any

import pytest

from autune_agent.main import registry
from autune_agent.main.ask import (
    BODY_CHARS,
    MAX_ROUNDS,
    MEETING_TOOLS,
    SIZE_LIMIT,
    TEAM_TOOLS,
    ask,
    compact,
    declare,
    tool_set,
)
from autune_agent.main.registry import CallBudget, RunScope, Tool, Toolbox
from autune_agent.main.toolcall import Declaration, FunctionCall
from autune_agent.results import ToolResult
from autune_agent.testing import ScriptedToolModel, mock_tool
from autune_core.errors import PrivacyViolationError

TEAM = RunScope(team_id="team_a")
MEETING = RunScope(team_id="team_a", meeting_id="mtg_1")


def _tool(name: str, fn: Any, doc: str = "Use this to test. More text here.") -> Tool:
    return Tool(name=name, description=doc, fn=fn)


def test_the_tool_set_follows_the_scope() -> None:
    assert tool_set(MEETING) == MEETING_TOOLS
    assert tool_set(TEAM) == TEAM_TOOLS
    assert len(MEETING_TOOLS) <= 9 and len(TEAM_TOOLS) <= 9


def test_team_id_is_never_declared_and_meeting_id_only_without_a_meeting() -> None:
    def gaps(session: Any, team_id: str, meeting_id: str) -> dict[str, Any]:
        return {}

    tools = {"gap.open_gaps": _tool("gap.open_gaps", gaps)}

    (in_meeting,) = declare(tools, MEETING)

    assert in_meeting.parameters["properties"] == {}
    assert "required" not in in_meeting.parameters
    assert declare(tools, TEAM) == []  # gap.open_gaps is not in the team set


def test_types_map_and_defaults_are_optional() -> None:
    def search(
        session: Any,
        team_id: str,
        query: str,
        limit: int = 5,
        exact: bool = False,
        terms: list[str] | None = None,
        meeting_id: str | None = None,
    ) -> dict[str, Any]:
        return {}

    (decl,) = declare(
        {"audio.search_team_meetings": _tool("audio.search_team_meetings", search)}, TEAM
    )

    assert decl.name == "audio__search_team_meetings"
    assert decl.parameters["properties"] == {
        "query": {"type": "STRING"},
        "limit": {"type": "INTEGER"},
        "exact": {"type": "BOOLEAN"},
        "terms": {"type": "ARRAY", "items": {"type": "STRING"}},
        "meeting_id": {"type": "STRING"},
    }
    assert decl.parameters["required"] == ["query"]


def test_the_description_is_the_first_sentence_cut_to_120() -> None:
    long = "Use this " + "x" * 200 + ". Second sentence."

    def recent(session: Any, team_id: str) -> dict[str, Any]:
        return {}

    (decl,) = declare({"audio.recent_meetings": _tool("audio.recent_meetings", recent, long)}, TEAM)

    assert len(decl.description) <= 120
    assert "Second" not in decl.description


def test_an_unmappable_parameter_drops_the_tool() -> None:
    def odd(session: Any, team_id: str, when: dict[str, Any]) -> dict[str, Any]:
        return {}

    assert declare({"audio.recent_meetings": _tool("audio.recent_meetings", odd)}, TEAM) == []


def test_tools_outside_the_set_are_not_declared() -> None:
    def anything(session: Any, team_id: str) -> dict[str, Any]:
        return {}

    assert (
        declare({"extraction.add_action_item": _tool("extraction.add_action_item", anything)}, TEAM)
        == []
    )


SESSION: Any = object()
OPEN = {
    "ok": True,
    "summary": "열린 액션 2건.",
    "items": [
        {"title": "API 문서", "body": "기한 10/9", "id": "act_1"},
        {"title": "QA", "id": "act_2"},
    ],
    "evidence": ["act_1", "act_2"],
}
DECL = [
    Declaration("extraction__open_action_items", "Use this.", {"type": "OBJECT", "properties": {}})
]


def _box(tools: dict[str, Any], scope: RunScope = TEAM) -> Toolbox:
    return Toolbox(tools, SESSION, CallBudget(), allowed=tools.keys(), scope=scope)


def test_one_call_then_done_returns_that_result() -> None:
    box = _box({"extraction.open_action_items": mock_tool("extraction.open_action_items", OPEN)})
    model = ScriptedToolModel([[FunctionCall("extraction__open_action_items", {})], "DONE"])

    result = ask("열린 액션?", model=model, toolbox=box, declarations=DECL)

    assert result.ok is True
    assert [i.title for i in result.items] == ["API 문서", "QA"]
    assert result.evidence == ["act_1", "act_2"]
    second = model.sent[1]["contents"]
    assert second[1]["role"] == "model"
    assert second[2]["parts"][0]["functionResponse"]["name"] == "extraction__open_action_items"


def test_no_call_at_all_is_an_empty_result() -> None:
    result = ask("안녕", model=ScriptedToolModel(["DONE"]), toolbox=_box({}), declarations=DECL)

    assert result.ok is False
    assert result.items == []


def test_a_model_that_never_stops_is_cut_at_three_rounds() -> None:
    box = _box({"extraction.open_action_items": mock_tool("extraction.open_action_items", OPEN)})
    model = ScriptedToolModel([[FunctionCall("extraction__open_action_items", {})]] * 10)

    result = ask("계속", model=model, toolbox=box, declarations=DECL)

    assert len(model.sent) == MAX_ROUNDS
    assert result.ok is True


def test_an_unknown_name_is_answered_and_the_loop_goes_on() -> None:
    box = _box({"extraction.open_action_items": mock_tool("extraction.open_action_items", OPEN)})
    model = ScriptedToolModel(
        [
            [FunctionCall("payroll__salaries", {})],
            [FunctionCall("extraction__open_action_items", {})],
            "DONE",
        ]
    )

    result = ask("월급?", model=model, toolbox=box, declarations=DECL)

    reply = model.sent[1]["contents"][2]["parts"][0]["functionResponse"]["response"]
    assert reply["ok"] is False and "not available" in reply["reason"]
    assert result.evidence == ["act_1", "act_2"]


def test_an_invented_meeting_is_refused_and_the_loop_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def gaps(session: Any, team_id: str, meeting_id: str) -> dict[str, Any]:
        return {"ok": True, "summary": "갭 없음"}

    monkeypatch.setattr(
        registry, "bind_scope", lambda *a, **k: ToolResult.failure("meeting not found")
    )
    box = _box({"gap.open_gaps": Tool("gap.open_gaps", "Use this.", gaps)})
    model = ScriptedToolModel(
        [[FunctionCall("gap__open_gaps", {"meeting_id": "mtg_made_up"})], "DONE"]
    )

    result = ask("갭?", model=model, toolbox=box, declarations=DECL)

    reply = model.sent[1]["contents"][2]["parts"][0]["functionResponse"]["response"]
    assert reply == {
        "ok": False,
        "reason": "meeting not found",
        "summary": "meeting not found",
        "items": [],
    }
    assert result.ok is False


def test_long_item_bodies_are_cut_before_they_go_back() -> None:
    long = {
        **OPEN,
        "items": [{"title": "긴 항목", "body": "가" * 500, "id": "act_1"}],
        "evidence": ["act_1"],
    }

    shown = compact(ToolResult.model_validate(long))

    assert len(shown["items"][0]["body"]) == BODY_CHARS
    assert shown["items"][0]["id"] == "act_1"


def test_a_request_that_would_pass_the_limit_is_not_sent() -> None:
    huge = [Declaration("x__y", "가" * (SIZE_LIMIT + 1), {"type": "OBJECT", "properties": {}})]
    model = ScriptedToolModel(["DONE"])

    result = ask("질문", model=model, toolbox=_box({}), declarations=huge)

    assert model.sent == []
    assert result.ok is False


def test_a_model_error_ends_the_loop_with_what_was_gathered() -> None:
    box = _box({"extraction.open_action_items": mock_tool("extraction.open_action_items", OPEN)})

    class Flaky(ScriptedToolModel):
        def step(self, *args: Any, **kwargs: Any) -> Any:
            if self.sent:
                raise RuntimeError("503 from the model")
            return super().step(*args, **kwargs)

    model = Flaky([[FunctionCall("extraction__open_action_items", {})]])

    result = ask("액션?", model=model, toolbox=box, declarations=DECL)

    assert result.ok is True and result.evidence == ["act_1", "act_2"]


def test_a_privacy_refusal_is_not_swallowed() -> None:
    class Refusing(ScriptedToolModel):
        def step(self, *args: Any, **kwargs: Any) -> Any:
            raise PrivacyViolationError("refusing to send unmasked personal data")

    with pytest.raises(PrivacyViolationError):
        ask("010-1234-5678", model=Refusing([]), toolbox=_box({}), declarations=DECL)


def test_at_most_three_calls_run_per_round() -> None:
    box = _box({"extraction.open_action_items": mock_tool("extraction.open_action_items", OPEN)})
    calls = [FunctionCall("extraction__open_action_items", {})] * 5
    model = ScriptedToolModel([calls, "DONE"])

    ask("많이", model=model, toolbox=box, declarations=DECL)

    responses = model.sent[1]["contents"][2]["parts"]
    assert len(responses) == 3
