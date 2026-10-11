"""A team's first member takes another member off (#552), against a real
database.

What is pinned: only the member who joined earliest of those still on the
team may remove; nobody removes themselves this way; a removal does exactly
what leaving does (``service._depart``) -- the membership and the pending
invitations they sent go, reading stops at once, what the team holds of them
stays and their own speech stays theirs to delete; and nobody is told.

Read ``conftest.py`` for ``db_session``: migrations once per session, each test
in a transaction that is rolled back.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_audio import invitations, service
from autune_audio.models import AudTeamInvitation
from autune_audio.router import router
from autune_core import (
    AutuneError,
    Meeting,
    Participant,
    Team,
    TeamMember,
    User,
    Utterance,
    get_session,
)
from autune_core.auth import current_user


def person(session: Session, email: str, name: str, *, team: str | None = None) -> User:
    user = User(email=email, display_name=name)
    session.add(user)
    session.flush()
    if team is not None:
        session.add(TeamMember(team_id=team, user_id=user.id))
        session.flush()
    return user


@pytest.fixture
def first(db_session: Session, team: str) -> User:
    """Joined first, so the one who may remove."""
    return person(db_session, "first@example.com", "먼저 온 사람", team=team)


@pytest.fixture
def mate(db_session: Session, team: str, first: User) -> User:
    return person(db_session, "mate@example.com", "같은 팀", team=team)


@pytest.fixture
def third(db_session: Session, team: str, mate: User) -> User:
    return person(db_session, "third@example.com", "세 번째", team=team)


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


def pending(session: Session, team: str) -> list[AudTeamInvitation]:
    session.expire_all()
    return list(
        session.scalars(sa.select(AudTeamInvitation).where(AudTeamInvitation.team_id == team))
    )


def remove(client_for, by: User, team: str, who: str):
    return client_for(by).delete(f"/api/audio/teams/{team}/members/{who}")


def can_remove(client_for, by: User, team: str):
    return client_for(by).get(f"/api/audio/teams/{team}/member-removal")


# --- who may ------------------------------------------------------------------


def test_the_first_member_removes_another_and_gets_the_list_as_it_stands(
    db_session: Session, client_for, first: User, mate: User, third: User, team: str
) -> None:
    response = remove(client_for, first, team, mate.id)

    assert response.status_code == 200, response.text
    assert {row["user_id"] for row in response.json()} == {first.id, third.id}
    assert members(db_session, team) == {first.id, third.id}


def test_a_member_who_is_not_first_is_refused(
    db_session: Session, client_for, first: User, mate: User, third: User, team: str
) -> None:
    for target in (third.id, first.id):
        response = remove(client_for, mate, team, target)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "not_first_team_member"
    assert members(db_session, team) == {first.id, mate.id, third.id}


def test_somebody_not_on_the_team_is_refused(
    db_session: Session, client_for, first: User, mate: User, team: str
) -> None:
    stranger = person(db_session, "stranger@example.com", "다른 사람")

    assert remove(client_for, stranger, team, mate.id).status_code == 403
    assert can_remove(client_for, stranger, team).status_code == 403
    assert members(db_session, team) == {first.id, mate.id}


def test_the_first_member_cannot_remove_themselves(
    db_session: Session, client_for, first: User, mate: User, team: str
) -> None:
    response = remove(client_for, first, team, first.id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "remove_self"
    assert members(db_session, team) == {first.id, mate.id}


def test_removing_somebody_not_on_the_team_is_404_and_changes_nothing(
    db_session: Session, client_for, first: User, mate: User, team: str
) -> None:
    elsewhere = person(db_session, "elsewhere@example.com", "다른 팀")

    assert remove(client_for, first, team, elsewhere.id).status_code == 404
    assert remove(client_for, first, team, "no-such-user").status_code == 404
    assert members(db_session, team) == {first.id, mate.id}


def test_when_the_first_member_leaves_the_next_earliest_may_remove(
    db_session: Session, client_for, first: User, mate: User, third: User, team: str
) -> None:
    assert set(can_remove(client_for, first, team).json()["removable"]) == {mate.id, third.id}
    assert can_remove(client_for, mate, team).json() == {"removable": []}

    assert client_for(first).delete(f"/api/audio/teams/{team}/members/me").status_code == 200

    assert can_remove(client_for, mate, team).json() == {"removable": [third.id]}
    assert remove(client_for, mate, team, third.id).status_code == 200
    assert members(db_session, team) == {mate.id}


def test_who_is_first_is_the_order_of_joining_not_of_name_or_account(
    db_session: Session, client_for, team: str
) -> None:
    """Accounts made in one order, joined in the other: joining decides."""
    older_account = person(db_session, "a@example.com", "가")
    newer_account = person(db_session, "b@example.com", "나", team=team)
    db_session.add(TeamMember(team_id=team, user_id=older_account.id))
    db_session.flush()

    assert remove(client_for, older_account, team, newer_account.id).status_code == 403
    assert remove(client_for, newer_account, team, older_account.id).status_code == 200


# --- what it does: what leaving does ------------------------------------------


def test_a_removal_runs_the_same_departure_as_leaving(
    db_session: Session,
    client_for,
    first: User,
    mate: User,
    team: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []
    real = service._depart

    def spy(session: Session, **kwargs) -> None:
        calls.append((kwargs["departing"].user_id, kwargs["removed_by"]))
        real(session, **kwargs)

    monkeypatch.setattr(service, "_depart", spy)

    assert remove(client_for, first, team, mate.id).status_code == 200
    assert calls == [(mate.id, first.id)]


def test_the_removed_member_can_no_longer_read_the_team_at_once(
    db_session: Session, client_for, first: User, mate: User, team: str
) -> None:
    meeting = Meeting(team_id=team, title="주간 회의")
    db_session.add(meeting)
    db_session.flush()
    reads = (
        f"/api/audio/teams/{team}/members",
        f"/api/audio/teams/{team}/invitations",
        f"/api/audio/meetings/{meeting.id}",
        f"/api/audio/transcripts/{meeting.id}",
        f"/api/audio/meetings?team_id={team}",
    )
    before = [client_for(mate).get(path).status_code for path in reads]

    remove(client_for, first, team, mate.id)

    after = [client_for(mate).get(path).status_code for path in reads]
    assert all(status != 403 for status in before), before
    assert after == [403] * len(reads)
    assert client_for(mate).get("/api/audio/teams").json() == []
    assert (
        client_for(mate)
        .post(f"/api/audio/teams/{team}/invitations", json={"email": "x@example.com"})
        .status_code
        == 403
    )


def test_the_pending_invitations_they_sent_go_and_their_links_stop_working(
    db_session: Session, client_for, first: User, mate: User, team: str
) -> None:
    by_address = client_for(mate).post(
        f"/api/audio/teams/{team}/invitations", json={"email": "guest@example.com"}
    )
    bearer = client_for(mate).post(f"/api/audio/teams/{team}/invitations", json={})
    client_for(first).post(
        f"/api/audio/teams/{team}/invitations", json={"email": "firsts-guest@example.com"}
    )
    guest = person(db_session, "guest@example.com", "받은 사람")
    holder = person(db_session, "holder@example.com", "링크를 가진 사람")

    remove(client_for, first, team, mate.id)

    assert [row.email for row in pending(db_session, team)] == ["firsts-guest@example.com"]
    for token, user in ((by_address.json()["token"], guest), (bearer.json()["token"], holder)):
        with pytest.raises(invitations.InvitationUnusableError):
            invitations.accept(db_session, token=token, user=user)
    assert members(db_session, team) == {first.id}


def test_what_the_team_holds_of_them_stays_and_their_speech_stays_theirs_to_delete(
    db_session: Session, client_for, first: User, mate: User, meeting: str, team: str
) -> None:
    participant = Participant(meeting_id=meeting, user_id=mate.id, speaker_label="SPEAKER_01")
    db_session.add(participant)
    db_session.flush()
    utterance = Utterance(
        meeting_id=meeting,
        participant_id=participant.id,
        speaker_label="SPEAKER_01",
        start_sec=0,
        end_sec=1,
        text="회의에서 한 말",
    )
    db_session.add(utterance)
    db_session.flush()

    remove(client_for, first, team, mate.id)

    db_session.expire_all()
    assert db_session.get(Meeting, meeting) is not None
    assert db_session.get(User, mate.id) is not None
    assert db_session.get(Participant, participant.id).user_id == mate.id
    response = client_for(mate).delete("/api/audio/me/speech")
    assert response.status_code == 200
    assert response.json()["utterances"] == 1


def test_their_other_teams_are_untouched(
    db_session: Session, client_for, first: User, mate: User, team: str
) -> None:
    other = Team(name="Other Team")
    db_session.add(other)
    db_session.flush()
    db_session.add(TeamMember(team_id=other.id, user_id=mate.id))
    db_session.flush()

    remove(client_for, first, team, mate.id)

    assert members(db_session, other.id) == {mate.id}
    assert [t["team_id"] for t in client_for(mate).get("/api/audio/teams").json()] == [other.id]


def test_removing_twice_is_404_the_second_time(
    db_session: Session, client_for, first: User, mate: User, team: str
) -> None:
    assert remove(client_for, first, team, mate.id).status_code == 200
    assert remove(client_for, first, team, mate.id).status_code == 404


def test_the_log_carries_ids_only_and_nobody_is_told(
    db_session: Session, client_for, first: User, mate: User, team: str
) -> None:
    """One departure event, the leaving one, with who removed. Nothing is sent:
    no route here calls a mail, Slack or notification path."""
    with capture_logs() as logs:
        remove(client_for, first, team, mate.id)

    (entry,) = [e for e in logs if e["event"] == "team_left"]
    assert (entry["team_id"], entry["user_id"], entry["removed_by"]) == (team, mate.id, first.id)
    assert "example.com" not in repr(logs)
    assert mate.display_name not in repr(logs) and first.display_name not in repr(logs)


def test_leaving_still_logs_no_remover(
    db_session: Session, client_for, first: User, mate: User, team: str
) -> None:
    with capture_logs() as logs:
        client_for(mate).delete(f"/api/audio/teams/{team}/members/me")

    (entry,) = [e for e in logs if e["event"] == "team_left"]
    assert entry["removed_by"] is None


def test_the_member_list_is_still_id_and_name_only(
    db_session: Session, client_for, first: User, mate: User, team: str
) -> None:
    """Who may remove is the caller's own answer, on its own route; the list
    says nothing new about anybody."""
    body = client_for(mate).get(f"/api/audio/teams/{team}/members").json()
    assert all(set(entry) == {"user_id", "name"} for entry in body)
    assert set(can_remove(client_for, mate, team).json()) == {"removable"}
