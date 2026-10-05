"""Pinning teams to the top of one's own list (the user, 2026-10-02), on Postgres.

A person on several teams sees them in the order they joined, and the screens
take the first as the default. A pin lets them say which come first: up to
three, in the order pinned, on their own membership and nobody else's.

Read ``conftest.py`` for ``db_session``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio import service
from autune_audio.router import router
from autune_core import AutuneError, Team, TeamMember, User, get_session
from autune_core.auth import current_user

NAMES = ("가 팀", "나 팀", "다 팀", "라 팀")


@pytest.fixture
def me(db_session: Session) -> User:
    user = User(email="pinner@example.com", display_name="고정하는 사람")
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def teams(db_session: Session, me: User) -> list[str]:
    """Four teams, joined in the order of ``NAMES``."""
    ids = []
    for name in NAMES:
        team = Team(name=name)
        db_session.add(team)
        db_session.flush()
        db_session.add(TeamMember(team_id=team.id, user_id=me.id))
        db_session.flush()
        ids.append(team.id)
    return ids


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


def names(body: list[dict]) -> list[str]:
    return [team["name"] for team in body]


def test_nothing_is_pinned_until_somebody_pins(client_for, me: User, teams: list[str]) -> None:
    body = client_for(me).get("/api/audio/teams").json()

    assert names(body) == list(NAMES)
    assert [team["pinned"] for team in body] == [False] * 4


def test_a_pinned_team_comes_first_and_the_answer_is_the_new_list(
    client_for, me: User, teams: list[str]
) -> None:
    response = client_for(me).put(f"/api/audio/teams/{teams[2]}/pin")

    assert response.status_code == 200
    assert names(response.json()) == ["다 팀", "가 팀", "나 팀", "라 팀"]
    assert [team["pinned"] for team in response.json()] == [True, False, False, False]
    assert client_for(me).get("/api/audio/teams").json() == response.json()


def test_pinned_teams_are_in_the_order_they_were_pinned(
    db_session: Session, me: User, teams: list[str]
) -> None:
    """Not by name and not by joining: ``라`` was pinned before ``나``."""
    start = datetime(2026, 10, 2, tzinfo=UTC)
    service.pin_team(db_session, team_id=teams[3], member=me, now=start)
    service.pin_team(db_session, team_id=teams[1], member=me, now=start + timedelta(minutes=1))

    listed = [team.name for team in service.teams_for(db_session, member=me)]

    assert listed == ["라 팀", "나 팀", "가 팀", "다 팀"]


def test_pinning_again_keeps_its_place(db_session: Session, me: User, teams: list[str]) -> None:
    start = datetime(2026, 10, 2, tzinfo=UTC)
    service.pin_team(db_session, team_id=teams[3], member=me, now=start)
    service.pin_team(db_session, team_id=teams[1], member=me, now=start + timedelta(minutes=1))

    service.pin_team(db_session, team_id=teams[3], member=me, now=start + timedelta(minutes=2))

    listed = [team.name for team in service.teams_for(db_session, member=me)]
    assert listed[:2] == ["라 팀", "나 팀"]


def test_a_fourth_pin_is_refused_and_pins_nothing(
    client_for, db_session: Session, me: User, teams: list[str]
) -> None:
    for team_id in teams[:3]:
        assert client_for(me).put(f"/api/audio/teams/{team_id}/pin").status_code == 200

    response = client_for(me).put(f"/api/audio/teams/{teams[3]}/pin")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "too_many_pinned_teams"
    assert response.json()["error"]["details"] == {"limit": 3}
    assert service.pinned_team_ids(db_session, member=me) == set(teams[:3])


def test_unpinning_puts_the_team_back_where_joining_put_it(
    client_for, me: User, teams: list[str]
) -> None:
    client_for(me).put(f"/api/audio/teams/{teams[2]}/pin")

    response = client_for(me).delete(f"/api/audio/teams/{teams[2]}/pin")

    assert response.status_code == 200
    assert names(response.json()) == list(NAMES)
    assert [team["pinned"] for team in response.json()] == [False] * 4
    # And a team that is not pinned can be unpinned without complaint.
    assert client_for(me).delete(f"/api/audio/teams/{teams[0]}/pin").status_code == 200


def test_unpinning_one_makes_room_for_another(client_for, me: User, teams: list[str]) -> None:
    for team_id in teams[:3]:
        client_for(me).put(f"/api/audio/teams/{team_id}/pin")
    client_for(me).delete(f"/api/audio/teams/{teams[0]}/pin")

    response = client_for(me).put(f"/api/audio/teams/{teams[3]}/pin")

    assert response.status_code == 200
    assert names(response.json()) == ["나 팀", "다 팀", "라 팀", "가 팀"]


def test_a_pin_is_one_persons_and_changes_nobody_elses_list(
    client_for, db_session: Session, me: User, teams: list[str]
) -> None:
    colleague = User(email="colleague@example.com", display_name="동료")
    db_session.add(colleague)
    db_session.flush()
    for team_id in teams[:2]:
        db_session.add(TeamMember(team_id=team_id, user_id=colleague.id))
        db_session.flush()

    client_for(me).put(f"/api/audio/teams/{teams[1]}/pin")

    theirs = client_for(colleague).get("/api/audio/teams").json()
    assert names(theirs) == ["가 팀", "나 팀"]
    assert [team["pinned"] for team in theirs] == [False, False]
    # Their own pin is theirs too: my unpinning a team does not take it off.
    client_for(colleague).put(f"/api/audio/teams/{teams[0]}/pin")
    client_for(me).delete(f"/api/audio/teams/{teams[0]}/pin")
    theirs = client_for(colleague).get("/api/audio/teams").json()
    assert [(team["name"], team["pinned"]) for team in theirs] == [
        ("가 팀", True),
        ("나 팀", False),
    ]
    # Nor does the member list say who pinned what.
    members = client_for(colleague).get(f"/api/audio/teams/{teams[1]}/members")
    assert all(set(entry) == {"user_id", "name"} for entry in members.json())


def test_only_a_member_can_pin_a_team(
    client_for, db_session: Session, me: User, teams: list[str]
) -> None:
    stranger = User(email="stranger@example.com", display_name="남")
    db_session.add(stranger)
    db_session.flush()

    assert client_for(stranger).put(f"/api/audio/teams/{teams[0]}/pin").status_code == 403
    assert client_for(stranger).delete(f"/api/audio/teams/{teams[0]}/pin").status_code == 403
    assert client_for(me).put("/api/audio/teams/team_that_does_not_exist/pin").status_code == 403
    assert (
        db_session.scalar(
            sa.select(sa.func.count())
            .select_from(TeamMember)
            .where(TeamMember.pinned_at.is_not(None))
            .where(TeamMember.team_id.in_(teams))
        )
        == 0
    )


def test_a_team_joined_later_goes_after_the_pinned_and_the_earlier(
    db_session: Session, me: User, teams: list[str]
) -> None:
    """What accepting an invitation does to the list: nothing at the top."""
    service.pin_team(db_session, team_id=teams[2], member=me)
    later = Team(name="가가 먼저 오는 이름")
    db_session.add(later)
    db_session.flush()
    db_session.add(TeamMember(team_id=later.id, user_id=me.id))
    db_session.flush()

    listed = [team.name for team in service.teams_for(db_session, member=me)]

    assert listed == ["다 팀", "가 팀", "나 팀", "라 팀", "가가 먼저 오는 이름"]
