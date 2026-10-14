"""A tool whose text no model may read (#1313).

A module lists such a read in ``TEXT_WITHHELD_TOOLS`` -- module B's
``find_materials``, an uploaded material's title and excerpt. The registry
marks every item it returns, the ask loop and the composer send a model the
summary and the items' ids, and the member who asked is shown the title and
the excerpt from the same items. Each test reads what would have left: the
body the model was handed.

Every value here is invented.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import replace
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from autune_agent import router as routes
from autune_agent.main import ask as ask_loop
from autune_agent.main.ask import MEETING_TOOLS, TEAM_TOOLS, ask, compact, declare
from autune_agent.main.gemini import GeminiRouter
from autune_agent.main.registry import (
    CallBudget,
    RunScope,
    Tool,
    Toolbox,
    ToolContractError,
    collect_tools,
)
from autune_agent.main.store import run_and_record
from autune_agent.main.toolcall import FunctionCall
from autune_agent.models import AgentRun
from autune_agent.results import Finding, SubagentResult, ToolResult
from autune_agent.testing import FakeRouter, ScriptedToolModel, example_subagent, mock_tool

from .test_routes import _client

TEAM = RunScope(team_id="team_a")
SESSION: Any = object()
MATERIALS = "extraction.find_materials"
WIRE = "extraction__find_materials"

TITLE = "분기 창고 이전 계획"
EXCERPT = "창고 이전은 삼월 둘째 주에 시작하고 재고 실사는 그 전 주에 끝낸다."
FOUND = {
    "ok": True,
    "summary": "올린 자료 2건에서 찾았습니다.",
    "items": [
        {"title": TITLE, "body": EXCERPT, "score": 0.71, "id": "mat_aa11", "position": 0},
        {"title": "하역 순서 메모", "body": "지게차 두 대로 나눈다.", "id": "mat_bb22"},
    ],
    "evidence": ["mat_aa11", "mat_bb22"],
}
MATERIAL_TEXT = (TITLE, EXCERPT, "창고", "삼월", "하역", "지게차")
OPEN = {
    "ok": True,
    "summary": "열린 할 일 1건.",
    "items": [{"title": "견적 받기", "body": "기한 10/20", "id": "act_1"}],
    "evidence": ["act_1"],
}
DECLARED = [
    ask_loop.Declaration(
        WIRE, "Use this.", {"type": "OBJECT", "properties": {"question": {"type": "STRING"}}}
    ),
    ask_loop.Declaration(
        "extraction__open_action_items", "Use this.", {"type": "OBJECT", "properties": {}}
    ),
]


def withheld_tool(payload: dict[str, Any] = FOUND) -> Tool:
    return replace(mock_tool(MATERIALS, payload), withheld=True)


def tools() -> dict[str, Tool]:
    return {
        MATERIALS: withheld_tool(),
        "extraction.open_action_items": mock_tool("extraction.open_action_items", OPEN),
    }


def box(held: dict[str, Tool], *, cuts: bool = True, budget: CallBudget | None = None) -> Toolbox:
    return Toolbox(
        held,
        SESSION,
        budget or CallBudget(),
        allowed=held.keys(),
        scope=TEAM,
        cuts_withheld_text=cuts,
    )


def said(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def no_material_text(value: Any) -> bool:
    text = said(value)
    return not any(word in text for word in MATERIAL_TEXT)


# --- the registry -----------------------------------------------------------------


def find_materials(_session: Any) -> dict[str, Any]:
    """Use this for the team's uploaded materials."""
    return FOUND


def open_items(_session: Any) -> dict[str, Any]:
    """Use this for the team's open items."""
    return OPEN


def _fake_tools_module(monkeypatch: pytest.MonkeyPatch, **attrs: Any) -> None:
    package = types.ModuleType("autune_fake")
    module = types.ModuleType("autune_fake.tools")
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, "autune_fake", package)
    monkeypatch.setitem(sys.modules, "autune_fake.tools", module)


def test_a_listed_tool_is_registered_and_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_tools_module(
        monkeypatch, TOOLS=[find_materials, open_items], TEXT_WITHHELD_TOOLS=[find_materials]
    )

    collected = collect_tools(["fake"])

    assert collected["fake.find_materials"].withheld is True
    assert collected["fake.open_items"].withheld is False


def test_an_unlisted_tool_that_reads_materials_by_its_name_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_tools_module(monkeypatch, TOOLS=[find_materials])

    with pytest.raises(ToolContractError, match="TEXT_WITHHELD_TOOLS"):
        collect_tools(["fake"])


def test_module_b_lists_its_material_search_and_no_other_module_has_one() -> None:
    marked = [name for name, tool in collect_tools().items() if tool.withheld]

    assert marked == [MATERIALS]


def test_every_item_of_a_listed_tool_is_marked_whatever_the_module_said() -> None:
    # The module's own dict cannot take the mark off.
    denied = {**FOUND, "items": [{**item, "withheld": False} for item in FOUND["items"]]}

    result = withheld_tool(denied)(SESSION)
    plain = mock_tool("extraction.open_action_items", OPEN)(SESSION)

    assert [item.withheld for item in result.items] == [True, True]
    assert [item.withheld for item in plain.items] == [False]
    # Whole for the member's screen: the mark hides nothing by itself.
    assert (result.items[0].title, result.items[0].body) == (TITLE, EXCERPT)


def test_the_mark_goes_where_the_item_goes() -> None:
    item = withheld_tool()(SESSION).items[0]

    again = SubagentResult.model_validate(
        SubagentResult(result=ToolResult(ok=True, summary="", items=[item])).model_dump()
    )

    assert again.result.items[0].withheld is True
    assert Finding(title="x").withheld is False


def test_a_caller_that_does_not_cut_the_text_is_not_given_the_tool() -> None:
    budget = CallBudget()
    plain = Toolbox(tools(), SESSION, budget, allowed=tools().keys(), scope=TEAM)

    result = plain.call(MATERIALS, question="창고")

    assert sorted(plain.describe()) == ["extraction.open_action_items"]
    assert result.ok is False and result.items == []
    assert result.reason == f"{MATERIALS} is not available here"
    assert sorted(box(tools()).describe()) == [MATERIALS, "extraction.open_action_items"]


def test_a_subagent_that_names_the_tool_does_not_get_it(
    session: Session, team: dict[str, str]
) -> None:
    # A subagent writes its own prompts, and nothing cuts a title out of them.
    reader = example_subagent("reader", (MATERIALS, "extraction.open_action_items"))

    _, state = run_and_record(
        "읽어 줘",
        session=session,
        router=FakeRouter({"읽어": "reader"}),
        team_id=team["team"],
        trigger={"kind": "chat"},
        subagents={"reader": reader},
        tools=tools(),
        notify=False,
    )

    outcome = state["outcome"]
    assert [item.title for item in outcome.result.items] == ["견적 받기"]
    assert no_material_text(outcome.model_dump())


# --- the ask loop -----------------------------------------------------------------


def test_the_team_set_offers_the_material_search_and_the_meeting_set_does_not() -> None:
    held = tools()

    team = [d.name for d in declare(held, TEAM)]
    meeting = [d.name for d in declare(held, RunScope(team_id="team_a", meeting_id="mtg_1"))]

    assert MATERIALS in TEAM_TOOLS and WIRE in team
    assert MATERIALS not in MEETING_TOOLS and WIRE not in meeting


def test_a_withheld_item_goes_back_to_the_model_as_its_id_alone() -> None:
    result = withheld_tool()(SESSION)

    back = compact(result)

    assert back == {
        "ok": True,
        "summary": "올린 자료 2건에서 찾았습니다.",
        "items": [{"id": "mat_aa11"}, {"id": "mat_bb22"}],
    }
    # An item of any other tool goes back as before.
    assert compact(mock_tool("extraction.open_action_items", OPEN)(SESSION))["items"] == [
        {"title": "견적 받기", "body": "기한 10/20", "id": "act_1"}
    ]


def test_nothing_the_model_is_handed_in_the_loop_holds_material_text() -> None:
    model = ScriptedToolModel(
        [
            [FunctionCall(WIRE, {"question": "이전 일정"})],
            [FunctionCall("extraction__open_action_items", {})],
            "DONE",
        ]
    )

    result = ask("이전 일정이 어떻게 돼?", model=model, toolbox=box(tools()), declarations=DECLARED)

    assert len(model.sent) == 3
    assert no_material_text(model.sent)
    # The model still learns what was found, and the other tool's text.
    assert "mat_aa11" in said(model.sent[1]) and "견적 받기" in said(model.sent[2])
    # The loop's result keeps both whole: it is what the member is shown.
    shown = {item.title: item for item in result.items}
    assert shown[TITLE].body == EXCERPT and shown[TITLE].withheld is True
    assert shown["견적 받기"].withheld is False


# --- composing the answer ---------------------------------------------------------


def composer(sent: list[dict[str, Any]]) -> GeminiRouter:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        payload = {"candidates": [{"content": {"parts": [{"text": "자료 2건을 찾았습니다."}]}}]}
        return httpx.Response(200, json=payload)

    router = GeminiRouter(api_key="k", model="gemini-test", base_url="https://llm.test/v1beta")
    router._client._client = httpx.Client(  # noqa: SLF001 - swap the transport only
        base_url="https://llm.test/v1beta", transport=httpx.MockTransport(handle)
    )
    return router


def test_composing_sends_no_line_for_a_withheld_item() -> None:
    sent: list[dict[str, Any]] = []
    model = ScriptedToolModel(
        [[FunctionCall(WIRE, {}), FunctionCall("extraction__open_action_items", {})], "DONE"]
    )
    result = ask("이전 일정?", model=model, toolbox=box(tools()), declarations=DECLARED)

    composer(sent).compose("이전 일정?", SubagentResult(result=result))

    [body] = sent
    text = body["contents"][0]["parts"][0]["text"]
    assert no_material_text(body)
    assert "올린 자료 2건에서 찾았습니다." in text
    assert "- 견적 받기: 기한 10/20" in text


# --- the run's row, and the reply ---------------------------------------------------


def test_the_runs_row_holds_the_tools_name_and_ids_and_no_material_text(
    session: Session, team: dict[str, str]
) -> None:
    sent: list[dict[str, Any]] = []
    model = ScriptedToolModel([[FunctionCall(WIRE, {"question": "이전 일정"})], "DONE"])

    row, state = run_and_record(
        "이전 일정이 어떻게 돼?",
        session=session,
        router=composer(sent),
        team_id=team["team"],
        trigger={"kind": "chat"},
        subagents={},
        tools=tools(),
        asker=model,
        notify=False,
    )

    assert row.route == "ask" and row.outcome == "answered"
    assert row.steps == [
        {"tool": MATERIALS, "ok": True, "evidence": ["mat_aa11", "mat_bb22"], "truncated": False}
    ]
    stored = session.get(AgentRun, row.id)
    assert stored is not None
    columns = {c.name: getattr(stored, c.name) for c in AgentRun.__table__.columns}
    assert no_material_text(columns)
    # Neither model was handed a title or an excerpt: not the loop's, not the composer's.
    assert len(sent) == 1 and no_material_text(sent) and no_material_text(model.sent)
    assert state["answer"] == "자료 2건을 찾았습니다."


def test_the_chat_reply_shows_the_member_the_title_and_the_excerpt(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("autune_agent.main.graph.collect_tools", tools)
    monkeypatch.setattr("autune_agent.main.graph.collect_own_tools", dict)
    model = ScriptedToolModel([[FunctionCall(WIRE, {"question": "이전 일정"})], "DONE"])
    client = _client(session, team["member"], chat_router=FakeRouter(), tool_model=model)

    reply = client.post(
        "/api/agent/chat", json={"team_id": team["team"], "message": "이전 일정이 어떻게 돼?"}
    )

    assert reply.status_code == 200
    body = reply.json()
    assert [(i["title"], i["body"], i["id"]) for i in body["items"]] == [
        (TITLE, EXCERPT, "mat_aa11"),
        ("하역 순서 메모", "지게차 두 대로 나눈다.", "mat_bb22"),
    ]
    assert no_material_text(model.sent)
    assert routes.ChatReply.model_fields["items"].annotation == list[Finding]
