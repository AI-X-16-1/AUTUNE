"""The live ticket: a minute-long token for a socket on another host.

The page asks for it over its own origin, where the session cookie goes, and
sends it in ``hello`` to a socket on the API's address, where the cookie does
not. It must be refused exactly where that socket would refuse the person.
"""

from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio import service
from autune_audio.router import router
from autune_core import AutuneError, TeamMember, User, get_session
from autune_core.auth import current_user, end_sessions, user_for_token
from autune_core.errors import PermissionDeniedError


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="member@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


@pytest.fixture
def outsider(db_session: Session) -> User:
    user = User(email="outsider@example.com", display_name="남")
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def client_for(db_session: Session):
    def build(user: User) -> TestClient:
        app = FastAPI()

        @app.exception_handler(AutuneError)
        async def render(_: Request, exc: AutuneError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        app.include_router(router, prefix="/api/audio")
        app.dependency_overrides[get_session] = lambda: db_session
        app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    return build


def test_a_member_gets_a_token_that_names_them(
    client_for, db_session: Session, meeting: str, member: User
) -> None:
    response = client_for(member).post(f"/api/audio/live/{meeting}/ticket")

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == 60
    assert user_for_token(db_session, body["token"]).id == member.id


def test_the_token_lives_a_minute(client_for, meeting: str, member: User) -> None:
    token = client_for(member).post(f"/api/audio/live/{meeting}/ticket").json()["token"]

    claims = jwt.decode(token, options={"verify_signature": False})
    left = claims["exp"] - datetime.now(UTC).timestamp()
    assert 0 < left <= 60


def test_an_outsider_gets_no_ticket(client_for, meeting: str, outsider: User) -> None:
    response = client_for(outsider).post(f"/api/audio/live/{meeting}/ticket")

    assert response.status_code == 403


def test_a_missing_meeting_gets_no_ticket(client_for, member: User) -> None:
    response = client_for(member).post("/api/audio/live/mtg_missing/ticket")

    assert response.status_code == 404


def test_signing_out_ends_a_ticket_too(db_session: Session, meeting: str, member: User) -> None:
    """#727: a ticket is a session token like any other."""
    token = service.live_ticket(db_session, user=member, meeting_id=meeting)
    end_sessions(member)
    db_session.flush()

    with pytest.raises(PermissionDeniedError):
        user_for_token(db_session, token)
