"""A token for a developer's browser, until there is a sign-in.

Every route that matters takes ``CurrentUser`` and the web client has no way to
get a token (#156, #189). This is the local-only bridge: name an email, get a
user, a team, a membership and a bearer token back. It is mounted under
``/dev`` and therefore only when ``AUTUNE_ENV=local`` — the same gate as the
upload page beside it.

The test that matters is the last one: the token actually opens a route that
takes ``CurrentUser``. A token that decodes but is refused at the door is not a
bridge.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio.dev import router as dev_router
from autune_audio.router import router as audio_router
from autune_core import AutuneError, Meeting, Team, TeamMember, User, get_session
from autune_core.auth import decode_token


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """Both routers, wired the way apps/api wires them — and ``current_user``
    left real, so the token has to work rather than be overridden."""
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(audio_router, prefix="/api/audio")
    app.include_router(dev_router, prefix="/api/audio/dev")
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)


def test_a_token_is_issued_for_a_new_person_and_their_team(
    client: TestClient, db_session: Session
) -> None:
    response = client.post(
        "/api/audio/dev/token", json={"email": "demo@example.com", "team_name": "데모 팀"}
    )

    assert response.status_code == 200
    body = response.json()
    assert decode_token(body["token"])["sub"] == body["user_id"]

    user = db_session.get(User, body["user_id"])
    team = db_session.get(Team, body["team_id"])
    assert user is not None and user.email == "demo@example.com"
    assert team is not None and team.name == "데모 팀"
    assert db_session.query(TeamMember).filter_by(user_id=user.id, team_id=team.id).one()


def test_the_same_email_gets_the_same_user_and_team(client: TestClient) -> None:
    """Idempotent, so a page reload or a second developer does not fork the demo."""
    first = client.post("/api/audio/dev/token", json={"email": "demo@example.com"}).json()
    second = client.post("/api/audio/dev/token", json={"email": "demo@example.com"}).json()

    assert first["user_id"] == second["user_id"]
    assert first["team_id"] == second["team_id"]


def test_the_token_opens_a_route_that_takes_current_user(
    client: TestClient, db_session: Session
) -> None:
    """The whole point. Without the header the transcript route is 403; with
    the token it is 200 — on a meeting in the team the token was issued for."""
    issued = client.post("/api/audio/dev/token", json={"email": "demo@example.com"}).json()
    meeting = Meeting(team_id=issued["team_id"], title="데모 회의")
    db_session.add(meeting)
    db_session.flush()

    refused = client.get(f"/api/audio/transcripts/{meeting.id}")
    allowed = client.get(
        f"/api/audio/transcripts/{meeting.id}",
        headers={"Authorization": f"Bearer {issued['token']}"},
    )

    assert refused.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json() == []
