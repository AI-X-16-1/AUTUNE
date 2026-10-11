"""Account deletion by the last member of a team takes the team with it (#817).

A team whose last member deleted their account used to be left with nobody
on it, and nothing could delete it or what it held. Now the account deletion
deletes it, through ``team_deletion.delete_team`` itself, once the request
names it as the last member's own team deletion asks (#1007). What is shown
here: confirmed, the team and its data go and every meeting's hooks run;
unconfirmed or misnamed, or with a meeting still being processed, nothing
goes and no hook runs; a team somebody else is on is only left; and a
mixture of the two.

Read ``conftest.py`` for ``db_session``: migrations once per session, each
test in a transaction that is rolled back.
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

from autune_audio.models import AudMaskingRule, AudTeamInvitation, TranscriptionJob
from autune_audio.router import router
from autune_core import (
    AutuneError,
    Meeting,
    Participant,
    Team,
    TeamMember,
    User,
    Utterance,
    deletion,
    get_session,
)
from autune_core.auth import current_user

SOLO = "고객사 A 프로젝트"
SHARED = "공유 팀"


def person(session: Session, email: str, *teams: str) -> User:
    user = User(email=email, display_name=email.split("@")[0])
    session.add(user)
    session.flush()
    for team in teams:
        session.add(TeamMember(team_id=team, user_id=user.id))
    session.flush()
    return user


def new_team(session: Session, name: str) -> str:
    row = Team(name=name)
    session.add(row)
    session.flush()
    return row.id


def held(session: Session, team: str, speaker: User, *, job: str = "done") -> str:
    """A meeting with one line by ``speaker`` and one attempt."""
    meeting = Meeting(team_id=team, title="주간 회의", status="complete")
    session.add(meeting)
    session.flush()
    participant = Participant(meeting_id=meeting.id, user_id=speaker.id, speaker_label="화자 1")
    session.add(participant)
    session.flush()
    session.add(
        Utterance(
            meeting_id=meeting.id,
            participant_id=participant.id,
            speaker_label="화자 1",
            start_sec=0,
            end_sec=1,
            text="안녕하세요",
        )
    )
    session.add(TranscriptionJob(meeting_id=meeting.id, status=job))
    session.flush()
    return meeting.id


def count(session: Session, model: type, *where: sa.ColumnElement[bool]) -> int:
    session.expire_all()
    return session.scalar(sa.select(sa.func.count()).select_from(model).where(*where)) or 0


@pytest.fixture
def solo(db_session: Session) -> str:
    return new_team(db_session, SOLO)


@pytest.fixture
def shared(db_session: Session) -> str:
    return new_team(db_session, SHARED)


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Every hook, recorded in order as (kind, id). The real ones open their
    own sessions and cannot see this test's rows; each module tests its own."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(deletion, "_user_hooks", {"r": lambda u: calls.append(("user", u))})
    monkeypatch.setattr(
        deletion, "_speech_hooks", {"r": lambda u, ids: calls.append(("speech", u))}
    )
    monkeypatch.setattr(deletion, "_meeting_hooks", {"r": lambda m: calls.append(("meeting", m))})
    return calls


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


def delete_me(client_for, user: User, teams: dict[str, str] | None = None):
    if teams is None:
        return client_for(user).request("DELETE", "/api/audio/me")
    body = {"teams": [{"team_id": t, "name": n} for t, n in teams.items()]}
    return client_for(user).request("DELETE", "/api/audio/me", json=body)


def untouched(session: Session, user: User, team: str, meetings: list[str]) -> None:
    session.expire_all()
    assert session.get(User, user.id) is not None
    assert session.get(Team, team) is not None
    assert count(session, TeamMember, TeamMember.team_id == team) >= 1
    assert count(session, Meeting, Meeting.id.in_(meetings)) == len(meetings)
    assert count(session, Utterance, Utterance.meeting_id.in_(meetings)) == len(meetings)


# --- confirmed ----------------------------------------------------------------


def test_the_last_member_confirms_and_the_team_goes_with_the_account(
    db_session: Session, client_for, solo: str, ran: list
) -> None:
    me = person(db_session, "me@example.com", solo)
    first = held(db_session, solo, me)
    second = held(db_session, solo, me, job="failed")
    db_session.add_all(
        [
            AudMaskingRule(team_id=solo, shape="A-#####", category="other", created_by=me.id),
            AudTeamInvitation(
                team_id=solo,
                email="invited@example.com",
                token_hash="1" * 64,
                invited_by=me.id,
                expires_at=datetime.now(tz=UTC) + timedelta(days=7),
            ),
        ]
    )
    db_session.flush()
    my_id = me.id

    with capture_logs() as logs:
        response = delete_me(client_for, me, {solo: SOLO})

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(User, my_id) is None
    assert db_session.get(Team, solo) is None
    for model, column in (
        (Meeting, Meeting.team_id),
        (AudMaskingRule, AudMaskingRule.team_id),
        (AudTeamInvitation, AudTeamInvitation.team_id),
        (TeamMember, TeamMember.team_id),
    ):
        assert count(db_session, model, column == solo) == 0, model.__tablename__
    assert count(db_session, Utterance, Utterance.meeting_id.in_([first, second])) == 0
    # Speech, then the person, then every meeting of the team before any row.
    assert ran[:2] == [("speech", my_id), ("user", my_id)]
    assert sorted(ran[2:]) == sorted([("meeting", first), ("meeting", second)])
    # The deletion is logged by ids; the team's name is not.
    assert [e["teams_deleted"] for e in logs if e["event"] == "audio_account_deleted"] == [1]
    assert any(e["event"] == "team_deleted" and e["team_id"] == solo for e in logs)
    assert SOLO not in str(logs)


def test_the_name_is_compared_as_the_teams_own_deletion_compares_it(
    db_session: Session, client_for, solo: str, ran: list
) -> None:
    me = person(db_session, "me@example.com", solo)

    response = delete_me(client_for, me, {solo: f"  {SOLO} "})

    assert response.status_code == 200, response.text
    assert db_session.get(Team, solo) is None


# --- not confirmed ------------------------------------------------------------


def test_without_a_confirmation_nothing_is_deleted_and_the_teams_are_listed(
    db_session: Session, client_for, solo: str, ran: list
) -> None:
    me = person(db_session, "me@example.com", solo)
    other_solo = new_team(db_session, "두 번째 팀")
    db_session.add(TeamMember(team_id=other_solo, user_id=me.id))
    meeting = held(db_session, solo, me)

    with capture_logs() as logs:
        response = delete_me(client_for, me)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "last_member_of_teams"
    assert sorted(error["details"]["teams"], key=lambda t: t["name"]) == [
        {"team_id": solo, "name": SOLO},
        {"team_id": other_solo, "name": "두 번째 팀"},
    ]
    # The names go to the person, not into the log.
    assert SOLO not in str(logs)
    assert ran == []
    untouched(db_session, me, solo, [meeting])
    assert db_session.get(Team, other_solo) is not None


def test_confirming_only_some_of_the_teams_deletes_nothing(
    db_session: Session, client_for, solo: str, ran: list
) -> None:
    me = person(db_session, "me@example.com", solo)
    other_solo = new_team(db_session, "두 번째 팀")
    db_session.add(TeamMember(team_id=other_solo, user_id=me.id))
    meeting = held(db_session, solo, me)

    response = delete_me(client_for, me, {solo: SOLO})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_member_of_teams"
    assert ran == []
    untouched(db_session, me, solo, [meeting])


def test_a_name_that_is_not_the_teams_deletes_nothing(
    db_session: Session, client_for, solo: str, ran: list
) -> None:
    me = person(db_session, "me@example.com", solo)
    meeting = held(db_session, solo, me)

    response = delete_me(client_for, me, {solo: "고객사 A"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "team_name_mismatch"
    assert SOLO not in response.text
    assert ran == []
    untouched(db_session, me, solo, [meeting])


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_meeting_still_being_processed_refuses_before_any_hook(
    db_session: Session, client_for, solo: str, ran: list, status: str
) -> None:
    me = person(db_session, "me@example.com", solo)
    meeting = held(db_session, solo, me, job=status)

    response = delete_me(client_for, me, {solo: SOLO})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "team_meeting_in_progress"
    assert ran == []
    untouched(db_session, me, solo, [meeting])


# --- teams with other members -------------------------------------------------


def test_a_team_somebody_else_is_on_is_only_left(
    db_session: Session, client_for, shared: str, ran: list
) -> None:
    me = person(db_session, "me@example.com", shared)
    mate = person(db_session, "mate@example.com", shared)
    meeting = held(db_session, shared, mate)

    response = delete_me(client_for, me)

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(User, me.id) is None
    untouched(db_session, mate, shared, [meeting])
    assert db_session.scalars(
        sa.select(TeamMember.user_id).where(TeamMember.team_id == shared)
    ).all() == [mate.id]
    assert ("meeting", meeting) not in ran


def test_naming_a_team_somebody_else_is_on_does_not_delete_it(
    db_session: Session, client_for, shared: str, ran: list
) -> None:
    me = person(db_session, "me@example.com", shared)
    mate = person(db_session, "mate@example.com", shared)
    meeting = held(db_session, shared, mate)

    response = delete_me(client_for, me, {shared: SHARED})

    assert response.status_code == 200, response.text
    untouched(db_session, mate, shared, [meeting])


def test_mixed_the_solo_team_goes_and_the_shared_one_stays(
    db_session: Session, client_for, solo: str, shared: str, ran: list
) -> None:
    me = person(db_session, "me@example.com", solo, shared)
    mate = person(db_session, "mate@example.com", shared)
    gone = held(db_session, solo, me)
    kept = held(db_session, shared, mate)

    without = delete_me(client_for, me)
    assert without.status_code == 409
    assert without.json()["error"]["details"]["teams"] == [{"team_id": solo, "name": SOLO}]
    assert ran == []

    response = delete_me(client_for, me, {solo: SOLO})

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(User, me.id) is None
    assert db_session.get(Team, solo) is None
    assert count(db_session, Meeting, Meeting.id == gone) == 0
    untouched(db_session, mate, shared, [kept])
    assert [m for kind, m in ran if kind == "meeting"] == [gone]


def test_somebody_who_joined_after_the_check_keeps_the_team(
    db_session: Session, client_for, solo: str, ran: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Between the check and ``delete_team``'s own count the team gained a
    member: it is not the person's alone any more, so they only leave it, and
    what ``delete_team`` had begun -- the pending invitations -- is undone."""
    me = person(db_session, "me@example.com", solo)
    joiner = person(db_session, "joiner@example.com")
    meeting = held(db_session, solo, joiner)
    # Not sent by the person leaving, so nothing but the team deletion would
    # take it: it is what shows that deletion undone.
    db_session.add(
        AudTeamInvitation(
            team_id=solo,
            email="invited@example.com",
            token_hash="2" * 64,
            invited_by=joiner.id,
            expires_at=datetime.now(tz=UTC) + timedelta(days=7),
        )
    )
    db_session.flush()

    def joins(user_id: str) -> None:
        db_session.add(TeamMember(team_id=solo, user_id=joiner.id))
        db_session.flush()

    monkeypatch.setattr(deletion, "_user_hooks", {"joins": joins})

    response = delete_me(client_for, me, {solo: SOLO})

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(User, me.id) is None
    untouched(db_session, joiner, solo, [meeting])
    assert count(db_session, AudTeamInvitation, AudTeamInvitation.team_id == solo) == 1
    assert ("meeting", meeting) not in ran
