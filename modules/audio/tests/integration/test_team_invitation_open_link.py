"""An invitation link made for no address (#552), against a real database.

The module owner's conditions of 2026-10-06, each pinned: it sits beside the
invitation for an address, which is unchanged; it works once; it lapses an hour
after it is made; an inviter has one open for a team and a new one replaces it;
the team's members see it in the pending list as a link with no address and
can take it back; only the token's hash is stored and log lines carry ids.

What it gives up is pinned too, so that nobody reads the feature as safer than
it is: whoever opens the link signed in joins.

Read ``conftest.py`` for ``db_session``: migrations once per session, each test
in a transaction that is rolled back.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_audio import invitations
from autune_audio.models import AudTeamInvitation
from autune_audio.router import router
from autune_core import AutuneError, Team, TeamMember, User, get_session
from autune_core.auth import current_user
from autune_core.errors import ConflictError


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


def link(client_for, by: User, team: str):
    """A link for no address: the body names none."""
    return client_for(by).post(f"/api/audio/teams/{team}/invitations", json={})


def addressed(client_for, by: User, team: str, email: str):
    return client_for(by).post(f"/api/audio/teams/{team}/invitations", json={"email": email})


def accept(client_for, by: User, token: str):
    return client_for(by).post("/api/audio/invitations/accept", json={"token": token})


def rows(session: Session, team: str) -> list[AudTeamInvitation]:
    session.expire_all()
    return list(
        session.scalars(
            sa.select(AudTeamInvitation)
            .where(AudTeamInvitation.team_id == team)
            .order_by(AudTeamInvitation.id)
        )
    )


def members(session: Session, team: str) -> set[str]:
    session.expire_all()
    return set(session.scalars(sa.select(TeamMember.user_id).where(TeamMember.team_id == team)))


# --- making one ---------------------------------------------------------------


def test_a_link_for_no_address_is_a_pending_row_that_names_nobody(
    db_session: Session, client_for, host: User, team: str
) -> None:
    users_before = db_session.scalar(sa.select(sa.func.count()).select_from(User))
    before = datetime.now(tz=UTC)

    response = link(client_for, host, team)

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"token", "expires_at", "emailed"}
    (row,) = rows(db_session, team)
    assert row.email is None
    assert row.invited_by == host.id
    # The token's hash and nothing else of it.
    assert row.token_hash == hashlib.sha256(body["token"].encode()).hexdigest()
    assert body["token"] not in repr(vars(row))
    assert members(db_session, team) == {host.id}
    assert db_session.scalar(sa.select(sa.func.count()).select_from(User)) == users_before
    # An hour, not the seven days of an invitation for an address.
    life = row.expires_at - before
    assert timedelta(minutes=59) < life <= timedelta(hours=1, seconds=5)


def test_an_invitation_for_an_address_still_has_its_seven_days(
    db_session: Session, client_for, host: User, team: str
) -> None:
    before = datetime.now(tz=UTC)

    addressed(client_for, host, team, "newcomer@example.com")

    (row,) = rows(db_session, team)
    assert row.email == "newcomer@example.com"
    assert timedelta(days=6, hours=23) < row.expires_at - before <= timedelta(days=7, seconds=5)


def test_only_a_member_of_the_team_can_make_one(
    db_session: Session, client_for, host: User, stranger: User, team: str
) -> None:
    assert link(client_for, stranger, team).status_code == 403
    assert rows(db_session, team) == []


def test_a_link_for_no_address_cannot_be_mailed(
    db_session: Session, client_for, host: User, team: str
) -> None:
    response = client_for(host).post(
        f"/api/audio/teams/{team}/invitations", json={"send_email": True}
    )

    assert response.status_code == 422
    assert rows(db_session, team) == []


def test_it_counts_towards_what_a_team_may_have_pending(
    db_session: Session, client_for, host: User, mate: User, team: str, monkeypatch
) -> None:
    monkeypatch.setattr(invitations, "MAX_PENDING", 2)
    assert addressed(client_for, host, team, "a@example.com").status_code == 201
    assert link(client_for, host, team).status_code == 201

    assert link(client_for, mate, team).status_code == 409
    assert len(rows(db_session, team)) == 2


# --- one open for an inviter and a team ---------------------------------------


def test_a_new_link_replaces_the_one_the_same_inviter_had_open(
    db_session: Session, client_for, host: User, stranger: User, team: str
) -> None:
    first = link(client_for, host, team).json()["token"]
    second = link(client_for, host, team).json()["token"]

    assert len(rows(db_session, team)) == 1
    assert accept(client_for, stranger, first).status_code == 404
    assert members(db_session, team) == {host.id}
    assert accept(client_for, stranger, second).status_code == 200


def test_it_replaces_nobody_elses_link_and_no_invitation_for_an_address(
    db_session: Session, client_for, host: User, mate: User, team: str
) -> None:
    other = Team(name="Other Team")
    db_session.add(other)
    db_session.flush()
    db_session.add(TeamMember(team_id=other.id, user_id=host.id))
    db_session.flush()
    addressed(client_for, host, team, "newcomer@example.com")
    link(client_for, mate, team)
    link(client_for, host, other.id)

    link(client_for, host, team)
    link(client_for, host, team)

    assert [(r.email, r.invited_by) for r in rows(db_session, team)] == [
        ("newcomer@example.com", host.id),
        (None, mate.id),
        (None, host.id),
    ]
    assert len(rows(db_session, other.id)) == 1


# --- using it -----------------------------------------------------------------


def test_whoever_opens_it_signed_in_joins_and_it_is_used_up(
    db_session: Session, client_for, host: User, stranger: User, team: str
) -> None:
    """What the link gives up, said plainly: nothing holds it to an address."""
    token = link(client_for, host, team).json()["token"]
    late = person(db_session, "late@example.com", "늦은 사람")

    response = accept(client_for, stranger, token)

    assert response.status_code == 200
    assert response.json()["team_id"] == team
    assert members(db_session, team) == {host.id, stranger.id}
    assert rows(db_session, team) == []
    # Once: the next person to open the same link is refused like any other.
    refused = accept(client_for, late, token)
    assert refused.status_code == 404
    assert refused.json()["error"]["code"] == "invitation_unusable"
    assert members(db_session, team) == {host.id, stranger.id}


def test_an_invitation_for_an_address_is_still_only_for_that_address(
    db_session: Session, client_for, host: User, stranger: User, team: str
) -> None:
    """The link for no address is an exception in one place only: a row with
    an address is checked exactly as before."""
    token = addressed(client_for, host, team, "newcomer@example.com").json()["token"]

    response = accept(client_for, stranger, token)

    assert response.status_code == 404
    assert members(db_session, team) == {host.id}
    assert len(rows(db_session, team)) == 1


def test_it_stops_working_an_hour_after_it_was_made(
    db_session: Session, client_for, host: User, stranger: User, team: str
) -> None:
    token = link(client_for, host, team).json()["token"]
    made = datetime.now(tz=UTC)

    with pytest.raises(invitations.InvitationUnusableError):
        invitations.accept(
            db_session, token=token, user=stranger, now=made + timedelta(hours=1, seconds=5)
        )
    assert members(db_session, team) == {host.id}

    # A minute before the hour is up it still works.
    invitations.accept(db_session, token=token, user=stranger, now=made + timedelta(minutes=59))
    assert members(db_session, team) == {host.id, stranger.id}


def test_a_lapsed_one_goes_with_the_sweep_and_with_the_next_invitation(
    db_session: Session, client_for, host: User, mate: User, team: str
) -> None:
    link(client_for, host, team)
    link(client_for, mate, team)
    soon = datetime.now(tz=UTC) + timedelta(hours=2)

    assert invitations.forget_expired(db_session, now=soon) == 2
    assert rows(db_session, team) == []

    link(client_for, host, team)
    db_session.execute(
        sa.update(AudTeamInvitation).values(expires_at=datetime.now(tz=UTC) - timedelta(minutes=1))
    )
    db_session.flush()
    addressed(client_for, mate, team, "newcomer@example.com")
    assert [r.email for r in rows(db_session, team)] == ["newcomer@example.com"]


# --- what the team sees, and takes back ---------------------------------------


def test_the_pending_list_shows_it_as_a_link_with_no_address(
    db_session: Session, client_for, host: User, mate: User, team: str
) -> None:
    token = link(client_for, host, team).json()["token"]
    (row,) = rows(db_session, team)

    response = client_for(mate).get(f"/api/audio/teams/{team}/invitations")

    (entry,) = response.json()
    assert entry["email"] is None
    assert entry["invited_by_name"] == "초대한 사람"
    assert entry["id"] == row.id
    assert token not in response.text and row.token_hash not in response.text


def test_any_member_can_take_it_back_and_then_it_admits_nobody(
    db_session: Session, client_for, host: User, mate: User, stranger: User, team: str
) -> None:
    token = link(client_for, host, team).json()["token"]
    (row,) = rows(db_session, team)

    response = client_for(mate).delete(f"/api/audio/teams/{team}/invitations/{row.id}")

    assert response.status_code == 200 and response.json() == []
    assert accept(client_for, stranger, token).status_code == 404
    assert members(db_session, team) == {host.id, mate.id}


# --- what is logged -----------------------------------------------------------


def test_the_log_carries_ids_and_never_the_token(
    db_session: Session, client_for, host: User, stranger: User, team: str
) -> None:
    with capture_logs() as logs:
        token = link(client_for, host, team).json()["token"]
        accept(client_for, stranger, token)

    made = next(e for e in logs if e["event"] == "team_invitation_created")
    used = next(e for e in logs if e["event"] == "team_invitation_accepted")
    assert made["addressed"] is False and used["addressed"] is False
    assert (made["team_id"], made["invited_by"], used["user_id"]) == (team, host.id, stranger.id)
    assert token not in repr(logs)
    assert hashlib.sha256(token.encode()).hexdigest() not in repr(logs)
    assert "example.com" not in repr(logs)


# --- two requests at once -----------------------------------------------------


@pytest.fixture
def committed(db_engine: sa.Engine) -> Iterator[tuple[str, str]]:
    with Session(db_engine) as session:
        team = Team(name="두 번 누른 팀")
        user = User(email="twice@example.com", display_name="두 번 누른 사람")
        session.add_all([team, user])
        session.flush()
        session.add(TeamMember(team_id=team.id, user_id=user.id))
        session.commit()
        ids = (team.id, user.id)
    try:
        yield ids
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == ids[0]))
            session.execute(sa.delete(User).where(User.id == ids[1]))
            session.commit()


def waiting_on_a_lock(engine: sa.Engine, pid: int) -> bool:
    with engine.connect() as probe:
        return bool(
            probe.execute(
                sa.text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            ).scalar()
        )


def test_two_requests_at_once_leave_one_link(
    db_engine: sa.Engine, committed: tuple[str, str]
) -> None:
    """One open link for an inviter and a team is the table's own rule, not
    only the order of two statements: the second request cannot see the first
    one's uncommitted row to replace it, so it has to be stopped at the insert.
    Two real sessions; ``pg_stat_activity`` is the witness that the second
    waited on the first."""
    team_id, user_id = committed
    outcome: list[BaseException | str] = []

    first = Session(db_engine)
    inviter = first.get(User, user_id)
    assert inviter is not None
    invitations.invite(first, team_id=team_id, email=None, by=inviter)  # not committed

    second = Session(db_engine)
    second_pid = second.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one()

    def double_click() -> None:
        try:
            other = second.get(User, user_id)
            assert other is not None
            invitations.invite(second, team_id=team_id, email=None, by=other)
            second.commit()
            outcome.append("two links")
        except BaseException as caught:
            second.rollback()
            outcome.append(caught)

    thread = threading.Thread(target=double_click)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not waiting_on_a_lock(db_engine, second_pid):
            assert thread.is_alive(), "the second request finished without waiting on the first"
            assert time.monotonic() < deadline, "the second request never waited"
            time.sleep(0.05)
        first.commit()
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        first.close()
        second.close()

    (result,) = outcome
    assert isinstance(result, ConflictError), result
    with Session(db_engine) as check:
        open_links = check.scalars(
            sa.select(AudTeamInvitation).where(
                AudTeamInvitation.team_id == team_id, AudTeamInvitation.email.is_(None)
            )
        ).all()
        assert len(open_links) == 1
