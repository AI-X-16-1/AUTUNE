"""The assistant searches a team's materials, and no model reads them (#1313).

The whole turn on PostgreSQL, with the layer's own registry, tool set, loop
and composer, and module B's own search: a member asks, the loop's model
calls ``extraction.find_materials``, and the answer is composed. What is
read here is every body either model was handed, the run's row, and what
the member is shown.

The two models are stand-ins that record what they are sent: nothing leaves
this process. Every value here is invented.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_agent.main.gemini import GeminiRouter
from autune_agent.main.store import run_and_record
from autune_agent.main.toolcall import FunctionCall
from autune_agent.models import AgentRun
from autune_agent.testing import ScriptedToolModel
from autune_core import Team
from autune_extraction import materials, tools
from autune_extraction.config import get_settings

MOVE_TITLE = "분기 창고 이전 계획"
MOVE = "창고 이전은 삼월 둘째 주에 시작하고 재고 실사는 그 전 주에 끝낸다.\n"
LOAD_TITLE = "하역 순서 메모"
LOAD = "창고 하역은 지게차 두 대로 나누고 이전 당일에는 출고를 멈춘다.\n"
THEIRS_TITLE = "남의 창고 견적"
QUESTION = "창고 이전 일정"
ASKED = "창고 이전이 언제야?"
MATERIAL_TEXT = (MOVE_TITLE, LOAD_TITLE, THEIRS_TITLE, "삼월", "실사", "하역", "지게차", "출고")
"""Words that are in a material and in neither the member's message nor the
question the loop's model wrote."""
WIRE = "extraction__find_materials"


@pytest.fixture(autouse=True)
def scale(monkeypatch: pytest.MonkeyPatch) -> None:
    """No floor and no cut: the fake embedder's scores are not on the real one's scale."""
    monkeypatch.setattr(get_settings(), "material_search_min_similarity", -1.0)
    monkeypatch.setattr(get_settings(), "material_search_max_below_best", 2.0)


def new_team(session: Session, name: str) -> str:
    team = Team(name=name)
    session.add(team)
    session.flush()
    return team.id


def upload(session: Session, team_id: str, body: str, title: str) -> str:
    return materials.store_upload(
        session, team_id, title=title, file_name="plan.txt", data=body.encode()
    ).row.id


def composer(sent: list[dict[str, Any]]) -> GeminiRouter:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        payload = {"candidates": [{"content": {"parts": [{"text": "자료를 찾았습니다."}]}}]}
        return httpx.Response(200, json=payload)

    router = GeminiRouter(api_key="k", model="gemini-test", base_url="https://llm.test/v1beta")
    router._client._client = httpx.Client(  # noqa: SLF001 - swap the transport only
        base_url="https://llm.test/v1beta", transport=httpx.MockTransport(handle)
    )
    return router


def said(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def turn(session: Session, team_id: str) -> tuple[AgentRun, Any, list[Any], list[dict[str, Any]]]:
    composed: list[dict[str, Any]] = []
    model = ScriptedToolModel([[FunctionCall(WIRE, {"question": QUESTION})], "DONE"])
    row, state = run_and_record(
        ASKED,
        session=session,
        router=composer(composed),
        team_id=team_id,
        trigger={"kind": "chat"},
        subagents={},
        asker=model,
        notify=False,
    )
    return row, state, model.sent, composed


def test_a_member_is_shown_the_materials_and_neither_model_reads_them(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "material_search_tool", True)
    team_id = new_team(db_session, "팀")
    move = upload(db_session, team_id, MOVE, MOVE_TITLE)
    load = upload(db_session, team_id, LOAD, LOAD_TITLE)
    theirs = upload(db_session, new_team(db_session, "다른 팀"), MOVE, THEIRS_TITLE)

    row, state, looped, composed = turn(db_session, team_id)

    # The loop's model was offered the tool, called it, and was answered.
    assert WIRE in said(looped[0]["tools"])
    answered = looped[1]["contents"][-1]["parts"][0]["functionResponse"]
    assert answered["name"] == WIRE
    back = answered["response"]
    assert back["ok"] is True
    assert sorted(item["id"] for item in back["items"]) == sorted([move, load])
    assert all(set(item) == {"id"} for item in back["items"])
    assert back["summary"] == f"올린 자료 2건에서 찾았습니다. {tools.SHOWN_BELOW}"
    # Neither model was handed a word of a material: not the loop's, in any
    # round, and not the one that writes the answer.
    assert len(looped) == 2 and len(composed) == 1
    outbound = said(looped) + said(composed)
    assert not any(word in outbound for word in MATERIAL_TEXT)
    assert tools.SHOWN_BELOW in said(composed)

    # The member is shown both, whole, and nothing of another team's.
    shown = {item.title: item for item in state["outcome"].result.items}
    assert set(shown) == {MOVE_TITLE, LOAD_TITLE}
    assert "삼월 둘째 주" in shown[MOVE_TITLE].body and "지게차" in shown[LOAD_TITLE].body
    assert all(item.withheld for item in shown.values())
    assert theirs not in said(state["outcome"].model_dump())

    # The run's row: the tool's name and the materials' ids. No title, no
    # excerpt, and nothing of the question either (#817 10(b)).
    assert row.steps == [
        {
            "tool": "extraction.find_materials",
            "ok": True,
            "evidence": row.steps[0]["evidence"],
            "truncated": False,
        }
    ]
    assert sorted(row.steps[0]["evidence"]) == sorted([move, load])
    kept = db_session.execute(sa.select(AgentRun.__table__).where(AgentRun.id == row.id)).one()
    stored = said(dict(kept._mapping))  # noqa: SLF001 - the row as the database holds it
    assert not any(word in stored for word in (*MATERIAL_TEXT, "창고", "이전"))


def test_with_the_switch_off_the_model_is_told_so_and_nothing_is_searched(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "material_search_tool", False)
    team_id = new_team(db_session, "팀")
    upload(db_session, team_id, MOVE, MOVE_TITLE)

    row, state, looped, composed = turn(db_session, team_id)

    back = looped[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert back == {
        "ok": False,
        "summary": "이 서버에서는 비서가 올린 자료를 찾지 않습니다.",
        "items": [],
        "reason": "material search is off for the assistant on this server",
    }
    assert state["outcome"].result.items == []
    assert row.steps[0]["evidence"] == []
    assert not any(word in said(looped) + said(composed) for word in MATERIAL_TEXT)
