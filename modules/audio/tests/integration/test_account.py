"""S29's buttons against a real database: what a person can see, copy and delete of their own.

Read `modules/audio/tests/integration/conftest.py` for `db_session`: it runs
`alembic upgrade heads` once per session and wraps each test in a transaction.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio.models import EMBEDDING_DIM, AudConsentAttestation, AudSpeakerEmbedding
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


def make_user(session: Session, team: str, email: str) -> User:
    user = User(email=email, display_name=email.split("@")[0])
    session.add(user)
    session.flush()
    session.add(TeamMember(team_id=team, user_id=user.id))
    session.flush()
    return user


@pytest.fixture
def me(db_session: Session, team: str) -> User:
    return make_user(db_session, team, "me@example.com")


@pytest.fixture
def colleague(db_session: Session, team: str) -> User:
    return make_user(db_session, team, "colleague@example.com")


@pytest.fixture
def spoken(db_session: Session, meeting: str, me: User, colleague: User) -> dict[str, str]:
    """One meeting where both spoke; ids of each one's participant and utterance."""
    ids: dict[str, str] = {}
    for who, label in ((me, "화자 1"), (colleague, "화자 2")):
        participant = Participant(meeting_id=meeting, user_id=who.id, speaker_label=label)
        db_session.add(participant)
        db_session.flush()
        utterance = Utterance(
            meeting_id=meeting,
            participant_id=participant.id,
            speaker_label=label,
            start_sec=0,
            end_sec=1,
            text=f"{label}의 말 010-****-5678",
        )
        db_session.add(utterance)
        db_session.flush()
        ids[f"{who.email}:participant"] = participant.id
        ids[f"{who.email}:utterance"] = utterance.id
    return ids


def profile(user_id: str) -> AudSpeakerEmbedding:
    return AudSpeakerEmbedding(
        user_id=user_id, vector=[1.0] + [0.0] * (EMBEDDING_DIM - 1), model_version="test"
    )


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


@pytest.fixture
def no_hooks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the registry with a recorder.

    The real hooks open their own sessions, which cannot see this test's
    uncommitted rows; what is asserted here is A's order, not B-E's cleanup.
    """
    ran: list[str] = []
    monkeypatch.setattr(deletion, "_user_hooks", {"recorder": ran.append})
    return ran


def test_my_data_counts_only_mine(
    db_session: Session, client_for, me: User, colleague: User, spoken: dict[str, str], meeting: str
) -> None:
    db_session.add(profile(me.id))
    db_session.add(profile(colleague.id))
    db_session.add(AudConsentAttestation(meeting_id=meeting, attested_by=me.id))
    db_session.flush()

    body = client_for(me).get("/api/audio/me/data").json()

    assert body["meetings_with_my_speech"] == 1
    assert body["voice_profile_rows"] == 1
    assert body["voice_profile_since"] is not None
    assert body["consents_attested"] == 1


def test_the_export_is_my_speech_and_nobody_elses(
    db_session: Session, client_for, me: User, spoken: dict[str, str]
) -> None:
    db_session.add(profile(me.id))
    db_session.flush()

    response = client_for(me).get("/api/audio/me/export")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    body = response.json()
    [meeting] = body["meetings"]
    assert [u["utterance_id"] for u in meeting["utterances"]] == [
        spoken["me@example.com:utterance"]
    ]
    assert body["user"]["email"] == "me@example.com"
    [voice] = body["voice_profile"]
    assert "vector" not in voice


def test_deleting_my_speech_leaves_the_meeting_and_everyone_else(
    db_session: Session, client_for, me: User, spoken: dict[str, str], meeting: str
) -> None:
    db_session.add(profile(me.id))
    db_session.flush()

    response = client_for(me).delete("/api/audio/me/speech")

    assert response.json() == {"utterances": 1, "voice_rows": 1}
    remaining = db_session.scalars(sa.select(Utterance.id)).all()
    assert remaining == [spoken["colleague@example.com:utterance"]]
    assert db_session.get(Meeting, meeting) is not None
    assert db_session.get(Participant, spoken["me@example.com:participant"]) is not None
    assert db_session.get(User, me.id) is not None


def test_deleting_my_account_takes_me_and_my_speech(
    db_session: Session, client_for, me: User, spoken: dict[str, str], no_hooks: list[str]
) -> None:
    db_session.add(profile(me.id))
    db_session.flush()
    my_id = me.id

    response = client_for(me).delete("/api/audio/me")

    assert response.status_code == 204
    assert "autune_session" in response.headers.get("set-cookie", "")
    assert no_hooks == [my_id]
    db_session.expire_all()
    assert db_session.get(User, my_id) is None
    assert db_session.scalars(sa.select(Utterance.id)).all() == [
        spoken["colleague@example.com:utterance"]
    ]
    assert db_session.scalars(sa.select(AudSpeakerEmbedding)).all() == []
    assert db_session.scalars(sa.select(TeamMember).where(TeamMember.user_id == my_id)).all() == []
    participant = db_session.get(Participant, spoken["me@example.com:participant"])
    assert participant is not None and participant.user_id is None


def test_my_speech_goes_even_though_a_hook_unlinks_me_first(
    db_session: Session,
    client_for,
    me: User,
    spoken: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A's real hook clears ``participants.user_id`` -- the only thing that says
    an utterance was mine. Found only by running the real hook on committed
    rows: read after it, nothing is mine and every word stays."""

    def unlink(user_id: str) -> None:
        db_session.execute(
            sa.update(Participant).where(Participant.user_id == user_id).values(user_id=None)
        )

    monkeypatch.setattr(deletion, "_user_hooks", {"audio": unlink})

    client_for(me).delete("/api/audio/me")

    assert db_session.get(Utterance, spoken["me@example.com:utterance"]) is None
    assert db_session.get(Utterance, spoken["colleague@example.com:utterance"]) is not None


def test_a_failing_hook_keeps_the_account(
    db_session: Session,
    client_for,
    me: User,
    spoken: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(user_id: str) -> None:
        raise RuntimeError("a module could not clean up")

    monkeypatch.setattr(deletion, "_user_hooks", {"elsewhere": broken})

    with pytest.raises(RuntimeError):
        client_for(me).delete("/api/audio/me")

    assert db_session.get(User, me.id) is not None
    assert db_session.get(Utterance, spoken["me@example.com:utterance"]) is not None


def test_a_member_reads_and_changes_the_teams_window(
    db_session: Session, client_for, me: User, team: str
) -> None:
    client = client_for(me)
    assert client.get(f"/api/audio/teams/{team}/privacy").json()["retention_days"] == 90

    response = client.patch(f"/api/audio/teams/{team}/privacy", json={"retention_days": 30})

    assert response.json() == {"team_id": team, "retention_days": 30}
    assert db_session.get(Team, team).retention_days == 30  # type: ignore[union-attr]


def test_only_the_offered_windows_are_accepted(client_for, me: User, team: str) -> None:
    response = client_for(me).patch(f"/api/audio/teams/{team}/privacy", json={"retention_days": 7})
    assert response.status_code == 422


def test_an_outsider_cannot_read_or_change_the_window(
    db_session: Session, client_for, team: str
) -> None:
    outsider = User(email="outsider@example.com", display_name="남")
    db_session.add(outsider)
    db_session.flush()
    client = client_for(outsider)

    assert client.get(f"/api/audio/teams/{team}/privacy").status_code == 403
    assert (
        client.patch(f"/api/audio/teams/{team}/privacy", json={"retention_days": 30}).status_code
        == 403
    )
