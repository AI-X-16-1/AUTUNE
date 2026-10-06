"""A team's pending invitations listed and cancelled (#552), against a real
database.

A follow-up the module owner left on #552. What is pinned: the list of pending
invitations is for the team's members, shows nothing a link could be rebuilt
from and nothing about whether an address has an account; and a cancelled link
stops working.

Read ``conftest.py`` for ``db_session``: migrations once per session, each test
in a transaction that is rolled back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_audio.models import AudTeamInvitation
from autune_audio.router import router
from autune_core import AutuneError, Team, TeamMember, User, get_session
from autune_core.auth import current_user

INVITED = "Newcomer@Example.com"


def person(session: Session, email: str, name: str, *, team: str | None = None) -> User:
    user = User(email=email, display_name=name)
    session.add(user)
    session.flush()
    if team is not None:
        session.add(TeamMember(team_id=team, user_id=user.id))
        session.flush()
    return user


@pytest.fixture
def host(db_session: Session, team: str) -> User:
    return person(db_session, "host@example.com", "초대한 사람", team=team)


@pytest.fixture
def mate(db_session: Session, team: str) -> User:
    return person(db_session, "mate@example.com", "같은 팀", team=team)


@pytest.fixture
def stranger(db_session: Session) -> User:
    return person(db_session, "stranger@example.com", "다른 사람")


@pytest.fixture
def client_for(db_session: Session):
    def build(user: User) -> TestClient:
        app = FastAPI()

        @app.exception_handler(AutuneError)
        async def _render(_: Request, exc: AutuneError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        app.include_router(router, prefix="/api/audio")
        app.dependency_overrides[get_session] = lambda: db_session
        app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    return build


def members(session: Session, team: str) -> set[str]:
    session.expire_all()
    return set(session.scalars(sa.select(TeamMember.user_id).where(TeamMember.team_id == team)))


def rows(session: Session, team: str) -> list[AudTeamInvitation]:
    session.expire_all()
    return list(
        session.scalars(sa.select(AudTeamInvitation).where(AudTeamInvitation.team_id == team))
    )


def invite(client_for, by: User, team: str, email: str = INVITED):
    return client_for(by).post(f"/api/audio/teams/{team}/invitations", json={"email": email})


def listed(client_for, by: User, team: str):
    return client_for(by).get(f"/api/audio/teams/{team}/invitations")


def cancel(client_for, by: User, team: str, invitation_id: int):
    return client_for(by).delete(f"/api/audio/teams/{team}/invitations/{invitation_id}")


# --- what is pending ----------------------------------------------------------


def test_a_member_sees_what_is_pending_and_nothing_a_link_could_be_made_from(
    db_session: Session, client_for, host: User, mate: User, team: str
) -> None:
    token = invite(client_for, host, team).json()["token"]
    (row,) = rows(db_session, team)

    response = listed(client_for, mate, team)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    (entry,) = response.json()
    assert entry["email"] == "newcomer@example.com"
    assert entry["invited_by_name"] == "초대한 사람"
    assert entry["id"] == row.id
    assert set(entry) == {"id", "email", "expires_at", "invited_by_name"}
    assert token not in response.text and row.token_hash not in response.text


def test_the_list_says_the_same_of_an_address_with_an_account_and_one_without(
    db_session: Session, client_for, host: User, mate: User, stranger: User, team: str
) -> None:
    invite(client_for, host, team, email=stranger.email)
    invite(client_for, host, team, email="nobody@example.com")

    first, second = listed(client_for, host, team).json()

    assert set(first) == set(second)
    assert {first["email"], second["email"]} == {"stranger@example.com", "nobody@example.com"}
    # Nothing from ``users`` about the invited address: no name, no id.
    assert stranger.display_name not in repr((first, second))
    assert stranger.id not in repr((first, second))


def test_someone_who_is_not_on_the_team_sees_nothing(
    db_session: Session, client_for, host: User, stranger: User, team: str
) -> None:
    invite(client_for, host, team)

    response = listed(client_for, stranger, team)

    assert response.status_code == 403
    assert "newcomer" not in response.text.lower()


def test_a_lapsed_invitation_is_not_listed_even_before_the_sweep(
    db_session: Session, client_for, host: User, team: str
) -> None:
    invite(client_for, host, team)
    db_session.execute(
        sa.update(AudTeamInvitation).values(expires_at=datetime.now(tz=UTC) - timedelta(minutes=1))
    )
    db_session.flush()

    assert listed(client_for, host, team).json() == []


# --- taking one back ----------------------------------------------------------


def test_a_cancelled_invitation_is_gone_and_its_link_stops_working(
    db_session: Session, client_for, host: User, mate: User, team: str
) -> None:
    token = invite(client_for, host, team).json()["token"]
    invitee = person(db_session, "newcomer@example.com", "받은 사람")
    (row,) = rows(db_session, team)

    # Any member of the team, not only the one who invited (as built; #552).
    response = cancel(client_for, mate, team, row.id)

    assert response.status_code == 200
    assert response.json() == []
    assert rows(db_session, team) == []
    refused = client_for(invitee).post("/api/audio/invitations/accept", json={"token": token})
    assert refused.status_code == 404
    assert members(db_session, team) == {host.id, mate.id}


def test_cancelling_what_is_no_longer_there_is_not_an_error(
    db_session: Session, client_for, host: User, team: str
) -> None:
    invite(client_for, host, team)
    (row,) = rows(db_session, team)

    assert cancel(client_for, host, team, row.id).status_code == 200
    assert cancel(client_for, host, team, row.id).status_code == 200
    assert cancel(client_for, host, team, 987654321).status_code == 200


def test_another_teams_invitation_is_not_cancelled_by_naming_its_id(
    db_session: Session, client_for, host: User, stranger: User, team: str
) -> None:
    other = Team(name="Other Team")
    db_session.add(other)
    db_session.flush()
    db_session.add(TeamMember(team_id=other.id, user_id=stranger.id))
    db_session.flush()
    invite(client_for, host, team)
    (row,) = rows(db_session, team)

    # A member of another team, through their own team's route.
    response = cancel(client_for, stranger, other.id, row.id)

    assert response.status_code == 200 and response.json() == []
    assert len(rows(db_session, team)) == 1
    # And through the invitation's own team, where they are nobody.
    assert cancel(client_for, stranger, team, row.id).status_code == 403
    assert len(rows(db_session, team)) == 1


def test_the_log_of_a_cancellation_carries_ids_and_never_the_address(
    db_session: Session, client_for, host: User, team: str
) -> None:
    invite(client_for, host, team)
    (row,) = rows(db_session, team)

    with capture_logs() as logs:
        cancel(client_for, host, team, row.id)

    (entry,) = [e for e in logs if e["event"] == "team_invitation_cancelled"]
    assert (entry["team_id"], entry["invitation_id"], entry["cancelled_by"]) == (
        team,
        row.id,
        host.id,
    )
    assert "newcomer" not in repr(logs).lower()
