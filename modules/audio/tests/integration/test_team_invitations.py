"""Team invitations the invitee accepts (#552), against a real database.

What review of #539 said an invitation must not do is what is pinned here: it
must not make anybody a member before they accept, must not tell the inviter
whether an address has an account or what its owner is called, must not make a
``users`` row for an address that never signed up, and must not say why a link
was refused.

Read ``conftest.py`` for ``db_session``: migrations once per session, each test
in a transaction that is rolled back.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_audio import account, invitations, retention
from autune_audio.models import AudTeamInvitation
from autune_audio.router import router
from autune_core import AutuneError, Team, TeamMember, User, deletion, get_session
from autune_core.auth import current_user

INVITED = "Newcomer@Example.com"
"""As somebody would type it. Stored and compared without case."""


def person(session: Session, email: str, name: str, *, team: str | None = None) -> User:
    user = User(email=email, display_name=name)
    session.add(user)
    session.flush()
    if team is not None:
        session.add(TeamMember(team_id=team, user_id=user.id))
        session.flush()
    return user


@pytest.fixture
def inviter(db_session: Session, team: str) -> User:
    return person(db_session, "host@example.com", "초대한 사람", team=team)


@pytest.fixture
def invitee(db_session: Session) -> User:
    """Signed up under the invited address, written in another case; on a team
    of their own whose name sorts after the inviter's."""
    own = Team(name="Zebra Solo")
    db_session.add(own)
    db_session.flush()
    return person(db_session, "newcomer@example.com", "받은 사람", team=own.id)


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


def invite(client_for, by: User, team: str, email: str = INVITED):
    return client_for(by).post(f"/api/audio/teams/{team}/invitations", json={"email": email})


def accept(client_for, by: User, token: str):
    return client_for(by).post("/api/audio/invitations/accept", json={"token": token})


def rows(session: Session, team: str) -> list[AudTeamInvitation]:
    session.expire_all()
    return list(
        session.scalars(sa.select(AudTeamInvitation).where(AudTeamInvitation.team_id == team))
    )


def members(session: Session, team: str) -> set[str]:
    return set(session.scalars(sa.select(TeamMember.user_id).where(TeamMember.team_id == team)))


# --- making an invitation -----------------------------------------------------


def test_an_invitation_is_a_pending_row_and_not_a_membership(
    db_session: Session, client_for, inviter: User, team: str
) -> None:
    users_before = db_session.scalar(sa.select(sa.func.count()).select_from(User))

    response = invite(client_for, inviter, team)

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"token", "expires_at", "emailed"}
    assert body["emailed"] is False  # nobody asked for mail
    assert len(body["token"]) >= 43  # 32 bytes, url-safe
    (row,) = rows(db_session, team)
    assert row.email == "newcomer@example.com"
    assert row.invited_by == inviter.id
    assert timedelta(days=6, hours=23) < row.expires_at - datetime.now(tz=UTC) <= timedelta(days=7)
    # Nobody joined, and no account was made for an address that has none.
    assert members(db_session, team) == {inviter.id}
    assert db_session.scalar(sa.select(sa.func.count()).select_from(User)) == users_before


def test_only_the_hash_of_the_token_is_stored(
    db_session: Session, client_for, inviter: User, team: str
) -> None:
    token = invite(client_for, inviter, team).json()["token"]

    (row,) = rows(db_session, team)
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    stored = db_session.execute(
        sa.text("SELECT * FROM aud_team_invitations WHERE id = :id"), {"id": row.id}
    ).one()
    assert token not in {str(value) for value in stored}


def test_the_answer_is_the_same_whatever_the_address(
    db_session: Session, client_for, inviter: User, invitee: User, team: str
) -> None:
    """An address with an account, one without, and one already on the team:
    the inviter must not be able to tell them apart, or learn a name."""
    answers = [
        invite(client_for, inviter, team, email)
        for email in (invitee.email, "nobody-signed-up@example.com", inviter.email)
    ]

    assert {response.status_code for response in answers} == {201}
    assert {frozenset(response.json()) for response in answers} == {
        frozenset({"token", "expires_at", "emailed"})
    }
    for response in answers:
        assert "받은 사람" not in response.text
        assert "초대한 사람" not in response.text


def test_someone_who_is_not_on_the_team_cannot_invite_to_it(
    db_session: Session, client_for, stranger: User, team: str
) -> None:
    assert invite(client_for, stranger, team).status_code == 403
    assert invite(client_for, stranger, "team_that_does_not_exist").status_code == 403
    assert rows(db_session, team) == []


@pytest.mark.parametrize(
    "email", ["", "no-at-sign", "a@b", "two@@example.com", "sp ace@example.com"]
)
def test_something_that_is_not_an_address_is_refused(
    db_session: Session, client_for, inviter: User, team: str, email: str
) -> None:
    assert invite(client_for, inviter, team, email).status_code == 422
    assert rows(db_session, team) == []


def test_inviting_the_same_address_again_replaces_the_link(
    db_session: Session, client_for, inviter: User, invitee: User, team: str
) -> None:
    first = invite(client_for, inviter, team).json()["token"]
    second = invite(client_for, inviter, team, INVITED.upper()).json()["token"]

    assert len(rows(db_session, team)) == 1
    assert accept(client_for, invitee, first).status_code == 404
    assert accept(client_for, invitee, second).status_code == 200


def test_a_team_cannot_park_addresses_without_bound(
    db_session: Session, client_for, inviter: User, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(invitations, "MAX_PENDING", 2)
    assert invite(client_for, inviter, team, "a@example.com").status_code == 201
    assert invite(client_for, inviter, team, "b@example.com").status_code == 201

    assert invite(client_for, inviter, team, "c@example.com").status_code == 409
    assert len(rows(db_session, team)) == 2


# --- before it is accepted ----------------------------------------------------


def test_nothing_changes_for_the_invited_person_until_they_accept(
    db_session: Session, client_for, inviter: User, invitee: User, team: str
) -> None:
    before = client_for(invitee).get("/api/audio/teams").json()

    invite(client_for, inviter, team)

    assert client_for(invitee).get("/api/audio/teams").json() == before
    assert client_for(invitee).get(f"/api/audio/teams/{team}/members").status_code == 403
    assert invitee.id not in members(db_session, team)


# --- accepting ----------------------------------------------------------------


def test_the_owner_of_the_address_joins_and_the_invitation_is_used_up(
    db_session: Session, client_for, inviter: User, invitee: User, team: str
) -> None:
    """The account signed up as ``newcomer@``; the invitation said ``Newcomer@``."""
    token = invite(client_for, inviter, team).json()["token"]

    response = accept(client_for, invitee, token)

    assert response.status_code == 200
    assert response.json() == {"team_id": team, "name": "Test Team", "pinned": False}
    assert members(db_session, team) == {inviter.id, invitee.id}
    assert rows(db_session, team) == []
    assert client_for(invitee).get(f"/api/audio/teams/{team}/members").status_code == 200


def test_an_account_whose_address_has_capitals_can_accept(
    db_session: Session, client_for, inviter: User, team: str
) -> None:
    """The other direction: Google gave the account ``Mixed.Case@``, and the
    inviter typed it in small letters."""
    mixed = person(db_session, "Mixed.Case@Example.com", "섞인 사람")
    token = invite(client_for, inviter, team, "mixed.case@example.com").json()["token"]

    assert accept(client_for, mixed, token).status_code == 200
    assert mixed.id in members(db_session, team)


def test_every_refusal_is_the_same_answer(
    db_session: Session, client_for, inviter: User, invitee: User, stranger: User, team: str
) -> None:
    """Another account, an expired link, a token that names nothing and a link
    already used: one status, one body, nothing that says which."""
    live = invite(client_for, inviter, team).json()["token"]
    other_account = accept(client_for, stranger, live)

    unknown = accept(client_for, invitee, "not-a-token-anybody-was-given")

    used_token = invite(client_for, inviter, team, "used@example.com").json()["token"]
    used_by = person(db_session, "used@example.com", "쓴 사람")
    assert accept(client_for, used_by, used_token).status_code == 200
    used = accept(client_for, used_by, used_token)

    lapsed_token = invite(client_for, inviter, team, "late@example.com").json()["token"]
    late = person(db_session, "late@example.com", "늦은 사람")
    db_session.execute(
        sa.update(AudTeamInvitation)
        .where(AudTeamInvitation.email == "late@example.com")
        .values(expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
    )
    expired = accept(client_for, late, lapsed_token)

    refusals = [other_account, unknown, used, expired]
    assert {response.status_code for response in refusals} == {404}
    assert len({response.text for response in refusals}) == 1
    assert refusals[0].json()["error"]["code"] == "invitation_unusable"
    # And none of them made a member.
    assert stranger.id not in members(db_session, team)
    assert late.id not in members(db_session, team)


def test_a_link_opened_by_another_account_still_works_for_the_right_one(
    db_session: Session, client_for, inviter: User, invitee: User, stranger: User, team: str
) -> None:
    token = invite(client_for, inviter, team).json()["token"]
    assert accept(client_for, stranger, token).status_code == 404

    assert accept(client_for, invitee, token).status_code == 200
    assert members(db_session, team) == {inviter.id, invitee.id}


def test_someone_already_on_the_team_succeeds_without_a_second_row(
    db_session: Session, client_for, inviter: User, team: str
) -> None:
    token = invite(client_for, inviter, team, inviter.email).json()["token"]

    assert accept(client_for, inviter, token).status_code == 200
    assert (
        db_session.scalar(
            sa.select(sa.func.count())
            .select_from(TeamMember)
            .where(TeamMember.team_id == team, TeamMember.user_id == inviter.id)
        )
        == 1
    )
    assert rows(db_session, team) == []


def test_accepting_does_not_change_the_team_a_person_starts_meetings_in(
    client_for, inviter: User, invitee: User, team: str
) -> None:
    """The screens take the first team as the default. ``Test Team`` sorts
    before ``Zebra Solo``; by name, accepting would have made the inviter's
    team the invitee's default for their next meeting."""
    own = client_for(invitee).get("/api/audio/teams").json()
    token = invite(client_for, inviter, team).json()["token"]

    accept(client_for, invitee, token)

    after = client_for(invitee).get("/api/audio/teams").json()
    assert after[0] == own[0]
    assert [entry["team_id"] for entry in after] == [own[0]["team_id"], team]


# --- what removes the address -------------------------------------------------


def test_the_sweep_removes_what_lapsed_and_keeps_what_did_not(
    db_session: Session, client_for, inviter: User, team: str
) -> None:
    invite(client_for, inviter, team, "live@example.com")
    invite(client_for, inviter, team, "late@example.com")
    now = datetime.now(tz=UTC)
    db_session.execute(
        sa.update(AudTeamInvitation)
        .where(AudTeamInvitation.email == "late@example.com")
        .values(expires_at=now - timedelta(seconds=1))
    )

    result = retention.sweep(db_session, now=now)

    assert result.invitations >= 1  # a database other suites committed to may hold more
    assert [row.email for row in rows(db_session, team)] == ["live@example.com"]


def test_the_next_invitation_clears_the_teams_lapsed_ones(
    db_session: Session, client_for, inviter: User, team: str
) -> None:
    invite(client_for, inviter, team, "late@example.com")
    db_session.execute(
        sa.update(AudTeamInvitation).values(expires_at=datetime.now(tz=UTC) - timedelta(days=1))
    )

    invite(client_for, inviter, team, "live@example.com")

    assert [row.email for row in rows(db_session, team)] == ["live@example.com"]


def test_invitations_go_with_the_inviters_account_and_with_the_team(
    db_session: Session, client_for, inviter: User, team: str
) -> None:
    other = Team(name="Other Team")
    db_session.add(other)
    db_session.flush()
    db_session.add(TeamMember(team_id=other.id, user_id=inviter.id))
    db_session.flush()
    invite(client_for, inviter, team, "a@example.com")
    invite(client_for, inviter, other.id, "b@example.com")
    host = person(db_session, "second-host@example.com", "둘째", team=other.id)
    invite(client_for, host, other.id, "c@example.com")

    db_session.execute(sa.delete(User).where(User.id == inviter.id))
    assert rows(db_session, team) == []
    assert [row.email for row in rows(db_session, other.id)] == ["c@example.com"]

    db_session.execute(sa.delete(Team).where(Team.id == other.id))
    assert rows(db_session, other.id) == []


def test_deleting_my_account_removes_the_invitations_addressed_to_me(
    db_session: Session,
    client_for,
    inviter: User,
    invitee: User,
    team: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real hooks open their own sessions and cannot see this test's rows.
    monkeypatch.setattr(deletion, "_user_hooks", {})
    monkeypatch.setattr(deletion, "_speech_hooks", {})
    invite(client_for, inviter, team)
    invite(client_for, inviter, team, "someone-else@example.com")

    account.delete_account(db_session, user=invitee)

    assert [row.email for row in rows(db_session, team)] == ["someone-else@example.com"]


# --- what the log may carry ---------------------------------------------------


def test_the_log_carries_ids_and_never_the_address_or_the_token(
    db_session: Session, client_for, inviter: User, invitee: User, stranger: User, team: str
) -> None:
    with capture_logs() as logs:
        token = invite(client_for, inviter, team).json()["token"]
        accept(client_for, stranger, token)
        accept(client_for, invitee, token)

    assert [entry["event"] for entry in logs if entry["event"].startswith("team_invitation")] == [
        "team_invitation_created",
        "team_invitation_refused",
        "team_invitation_accepted",
    ]
    written = repr(logs).lower()
    assert "newcomer@example.com" not in written
    assert "stranger@example.com" not in written
    assert token.lower() not in written
    assert hashlib.sha256(token.encode()).hexdigest() not in written
