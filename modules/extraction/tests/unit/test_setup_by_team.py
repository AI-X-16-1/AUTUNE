"""B's integration-setup routes take the team itself, for S28 settings (#496).

The 액션 tab names a meeting; the settings page has none and names the team.
Same check either way: a member gets the team's answer, anyone else the 404 an
unknown meeting or team gets (#189).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Meeting, Team, TeamMember, User, current_user, get_session
from autune_core.errors import AutuneError
from autune_extraction import notion_connect
from autune_extraction.router import router


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine, tables=[User.__table__, Team.__table__, TeamMember.__table__, Meeting.__table__]
    )
    with Session(engine) as s:
        s.add_all([Team(id="team_1", name="A"), Team(id="team_2", name="B")])
        s.add_all(
            [User(id=u, email=f"{u}@example.com", display_name=u) for u in ("user_in", "user_out")]
        )
        s.flush()
        s.add_all(
            [
                TeamMember(team_id="team_1", user_id="user_in"),
                TeamMember(team_id="team_2", user_id="user_out"),
            ]
        )
        s.commit()
        yield s


def client(
    session: Session, user_id: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, list[str]]:
    asked: list[str] = []

    def pages_for(team_id: str) -> dict[str, Any]:
        asked.append(team_id)
        return {"connected": False}

    monkeypatch.setattr(notion_connect, "pages_for", pages_for)
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/extraction")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    return TestClient(app), asked


def test_a_member_names_the_team(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    http, asked = client(session, "user_in", monkeypatch)

    assert http.get("/api/extraction/notion/setup", params={"team_id": "team_1"}).status_code == 200
    assert asked == ["team_1"]


@pytest.mark.parametrize("team", ["team_1", "team_nope", None])
def test_anyone_else_gets_a_404(
    session: Session, monkeypatch: pytest.MonkeyPatch, team: str | None
) -> None:
    http, asked = client(session, "user_out", monkeypatch)
    params = {"team_id": team} if team else {}

    assert http.get("/api/extraction/notion/setup", params=params).status_code == 404
    assert asked == []
