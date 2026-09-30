"""Creating a workspace (S02): a team, its creator's membership, and invitations.

A person who signs in with Google and belongs to no team could do nothing: the
create-meeting call takes a ``team_id`` and there was no way to make a team
outside the dev-token route. ``POST /teams`` is that way. Teams and
memberships are shared entities module A writes (invariant 4).

An invitation is a membership for an email address. When the invited person
has no account yet, a ``users`` row is made for the address; Google sign-in
adopts a row by email (``upsert_user_from_google``), so the first time they
sign in they are already on the team. No email is sent.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio.router import router
from autune_core import AutuneError, Team, TeamMember, User, get_session
from autune_core.auth import current_user


@pytest.fixture
def newcomer(db_session: Session) -> User:
    user = User(email="founder@example.com", display_name="창업자")
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def app_for(db_session: Session):
    def _build(user: User | None) -> TestClient:
        app = FastAPI()

        @app.exception_handler(AutuneError)
        async def _render(_: Request, exc: AutuneError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        app.include_router(router, prefix="/api/audio")
        app.dependency_overrides[get_session] = lambda: db_session
        if user is not None:
            app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    return _build


@pytest.fixture
def client(app_for, newcomer: User) -> TestClient:
    return app_for(newcomer)


def _members(db_session: Session, team_id: str) -> dict[str, str | None]:
    rows = db_session.execute(
        sa.select(User.email, TeamMember.role)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id)
    )
    return {email: role for email, role in rows}


def test_creating_a_team_makes_the_creator_a_member_with_their_role(
    client: TestClient, db_session: Session
) -> None:
    response = client.post("/api/audio/teams", json={"name": "검색 스쿼드", "role": "PM"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "검색 스쿼드"
    assert db_session.get(Team, body["team_id"]) is not None
    assert _members(db_session, body["team_id"]) == {"founder@example.com": "PM"}


def test_the_new_team_is_listed_for_its_creator(client: TestClient) -> None:
    team_id = client.post("/api/audio/teams", json={"name": "검색 스쿼드"}).json()["team_id"]

    assert client.get("/api/audio/teams").json() == [{"team_id": team_id, "name": "검색 스쿼드"}]


def test_an_invited_address_with_no_account_gets_one_on_the_team(
    client: TestClient, db_session: Session
) -> None:
    team_id = client.post(
        "/api/audio/teams",
        json={"name": "검색 스쿼드", "invite_emails": ["Lee.Dev@Corp.com"]},
    ).json()["team_id"]

    invited = db_session.scalar(sa.select(User).where(User.email == "lee.dev@corp.com"))
    assert invited is not None
    assert invited.google_sub is None
    assert "lee.dev@corp.com" in _members(db_session, team_id)


def test_an_invited_address_with_an_account_joins_without_a_second_row(
    client: TestClient, db_session: Session
) -> None:
    existing = User(email="choi@corp.com", display_name="최디자인")
    db_session.add(existing)
    db_session.flush()

    team_id = client.post(
        "/api/audio/teams",
        json={"name": "검색 스쿼드", "invite_emails": ["choi@corp.com", "CHOI@corp.com"]},
    ).json()["team_id"]

    count = db_session.scalar(
        sa.select(sa.func.count()).select_from(User).where(User.email == "choi@corp.com")
    )
    assert count == 1
    assert set(_members(db_session, team_id)) == {"founder@example.com", "choi@corp.com"}


def test_inviting_yourself_does_not_add_you_twice(client: TestClient, db_session: Session) -> None:
    team_id = client.post(
        "/api/audio/teams",
        json={"name": "검색 스쿼드", "role": "Data", "invite_emails": ["founder@example.com"]},
    ).json()["team_id"]

    assert _members(db_session, team_id) == {"founder@example.com": "Data"}


@pytest.mark.parametrize("name", ["", "a", " ", "x" * 41])
def test_a_name_outside_two_to_forty_characters_is_refused(client: TestClient, name: str) -> None:
    assert client.post("/api/audio/teams", json={"name": name}).status_code == 422


def test_a_malformed_invite_address_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/audio/teams", json={"name": "검색 스쿼드", "invite_emails": ["not-an-email"]}
    )
    assert response.status_code == 422


def test_creating_a_team_without_a_token_is_refused(app_for) -> None:
    assert app_for(None).post("/api/audio/teams", json={"name": "검색 스쿼드"}).status_code == 403
