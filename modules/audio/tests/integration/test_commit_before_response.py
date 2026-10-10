"""A write is committed before its response leaves.

``get_session`` commits after its ``yield``, and FastAPI runs that teardown
*after* the response is sent. A browser that acts on a 201 at once could then
read the row before it existed: "새 회의" got its meeting id, opened ``/live``,
and ``GET /meetings/{id}`` answered 404 "meeting ... not found" on the dev
server. The row was there a moment later.

The test session's override never commits, so a commit counted here is one the
route made itself, inside the request.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio.live import routes as live_routes
from autune_audio.router import router
from autune_core import AutuneError, TeamMember, User, get_session
from autune_core.auth import current_user


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="member@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


@pytest.fixture
def commits(db_session: Session) -> list[int]:
    seen: list[int] = []
    sa.event.listen(db_session, "after_commit", lambda _: seen.append(1))
    return seen


@pytest.fixture
def client(db_session: Session, member: User) -> TestClient:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/audio")
    app.include_router(live_routes.router, prefix="/api/audio")
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[current_user] = lambda: member
    return TestClient(app)


CALLS: dict[str, Callable[[TestClient, str, str], object]] = {
    "create a meeting": lambda c, team, _: c.post(
        "/api/audio/meetings",
        json={"title": "주간 회의", "team_id": team, "started_at": "2026-10-08T06:00:00Z"},
    ),
    "create a team": lambda c, *_: c.post("/api/audio/teams", json={"name": "새 팀"}),
    "pin a team": lambda c, team, _: c.put(f"/api/audio/teams/{team}/pin"),
    "unpin a team": lambda c, team, _: c.delete(f"/api/audio/teams/{team}/pin"),
    "take a live ticket": lambda c, _, meeting: c.post(f"/api/audio/live/{meeting}/ticket"),
}


@pytest.mark.parametrize("name", list(CALLS))
def test_the_route_commits_before_it_answers(
    client: TestClient, team: str, meeting: str, commits: list[int], name: str
) -> None:
    response = CALLS[name](client, team, meeting)

    assert response.status_code < 300, response.text  # type: ignore[attr-defined]
    assert commits, f"{name}: answered before anything was committed"
