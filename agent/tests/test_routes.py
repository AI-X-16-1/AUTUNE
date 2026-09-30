"""``/api/agent``: members only, one row per turn, and a clear answer when the layer is off."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_agent import router as routes
from autune_agent.models import AgentApprover, AgentResearchDocument, AgentRun
from autune_agent.testing import FakeRouter
from autune_core import AutuneError, User, current_user, get_session


def _client(session: Session, user_id: str, *, chat_router: object | None) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")

    @app.exception_handler(AutuneError)
    async def _error(_: object, exc: AutuneError) -> object:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    if chat_router is not None:
        app.dependency_overrides[routes.get_chat_router] = lambda: chat_router
    return TestClient(app)


@pytest.fixture
def member(session: Session, team: dict[str, str]) -> Iterator[TestClient]:
    yield _client(session, team["member"], chat_router=FakeRouter())


def test_a_member_chats_and_the_turn_is_recorded(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    # No subagent is built yet, so every turn is unrouted -- and still recorded.
    reply = member.post("/api/agent/chat", json={"team_id": team["team"], "message": "안녕"})

    assert reply.status_code == 200
    body = reply.json()
    assert body["outcome"] == "unrouted"
    assert body["answer"] == "no subagent fits this request"
    run = session.get(AgentRun, body["run_id"])
    assert run is not None
    assert run.requested_by == team["member"]
    assert run.trigger == {"kind": "chat"}


def test_a_non_member_is_refused(session: Session, team: dict[str, str]) -> None:
    outsider = _client(session, team["outsider"], chat_router=FakeRouter())

    reply = outsider.post("/api/agent/chat", json={"team_id": team["team"], "message": "안녕"})

    assert reply.status_code == 403
    assert session.query(AgentRun).count() == 0


def test_a_pasted_transcript_is_refused(member: TestClient, team: dict[str, str]) -> None:
    reply = member.post("/api/agent/chat", json={"team_id": team["team"], "message": "가" * 1001})

    assert reply.status_code == 422


def test_the_timeline_lists_the_team_runs_newest_first(
    member: TestClient, team: dict[str, str]
) -> None:
    for message in ("하나", "둘"):
        member.post("/api/agent/chat", json={"team_id": team["team"], "message": message})

    runs = member.get("/api/agent/runs", params={"team_id": team["team"]}).json()

    assert len(runs) == 2
    assert {r["outcome"] for r in runs} == {"unrouted"}
    assert "answer" not in runs[0]


def test_without_a_key_the_layer_says_so(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTUNE_AGENT_LLM_API_KEY", "")
    routes.get_agent_settings.cache_clear()
    client = _client(session, team["member"], chat_router=None)

    reply = client.post("/api/agent/chat", json={"team_id": team["team"], "message": "안녕"})

    assert reply.status_code == 500
    assert "AUTUNE_AGENT_LLM_API_KEY" in reply.text
    routes.get_agent_settings.cache_clear()


def _doc(session: Session, team: dict[str, str], status: str) -> str:
    doc = AgentResearchDocument(
        team_id=team["team"], meeting_id=team["meeting"], body=f"{status} 본문", status=status
    )
    session.add(doc)
    session.commit()
    return doc.id


def _research(client: TestClient, team: dict[str, str]) -> Any:
    return client.get(
        "/api/agent/research", params={"team_id": team["team"], "meeting_id": team["meeting"]}
    )


def test_a_member_sees_approved_documents_only(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    _doc(session, team, "approved")
    _doc(session, team, "proposed")

    assert [d["status"] for d in _research(member, team).json()] == ["approved"]


def test_a_research_approver_also_sees_proposals(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope="research"))
    session.commit()
    _doc(session, team, "proposed")

    assert [d["status"] for d in _research(member, team).json()] == ["proposed"]


def test_research_is_refused_to_a_non_member(session: Session, team: dict[str, str]) -> None:
    outsider = _client(session, team["outsider"], chat_router=FakeRouter())

    assert _research(outsider, team).status_code == 403
