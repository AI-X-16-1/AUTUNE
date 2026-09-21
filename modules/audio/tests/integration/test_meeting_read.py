"""Reading a meeting's own row, and the teams a person may open one for.

Screen S12 follows a meeting from ``analyzing`` to ``complete`` and there was
no endpoint that said which it was: ``/transcripts/{id}`` returns an empty list
all the way through the task, and a screen cannot tell "not yet" from "nobody
spoke" without the status (#259, ``transcript_for_meeting``). ``/teams`` exists
because the create-meeting call takes a ``team_id`` and a browser with only a
token has no way to learn one.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio.router import router
from autune_core import AutuneError, Meeting, Team, TeamMember, User, get_session
from autune_core.auth import current_user


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
def client(app_for, member: User) -> Iterator[TestClient]:
    yield app_for(member)


def test_a_member_reads_the_meetings_status_and_flags(
    client: TestClient, db_session: Session, meeting: str
) -> None:
    """What S12 polls. The two flags are what the pipeline actually wrote, so
    the screen can say "original deleted" and "masked" from stored state
    rather than from having reached a stage."""
    row = db_session.get(Meeting, meeting)
    row.status = "complete"
    row.original_audio_deleted = True
    row.pii_masked = True
    db_session.flush()

    body = client.get(f"/api/audio/meetings/{meeting}").json()

    assert body == {
        "meeting_id": meeting,
        "title": "Test Meeting",
        "status": "complete",
        "original_audio_deleted": True,
        "pii_masked": True,
    }


def test_reading_another_teams_meeting_is_refused(app_for, outsider: User, meeting: str) -> None:
    assert app_for(outsider).get(f"/api/audio/meetings/{meeting}").status_code == 403


def test_reading_a_meeting_that_does_not_exist(client: TestClient) -> None:
    assert client.get("/api/audio/meetings/mtg_nope").status_code == 404


def test_reading_without_a_token_is_refused(app_for, meeting: str) -> None:
    assert app_for(None).get(f"/api/audio/meetings/{meeting}").status_code == 403


def test_a_member_lists_only_their_own_teams(
    client: TestClient, db_session: Session, team: str
) -> None:
    other = Team(name="Somebody Else's Team")
    db_session.add(other)
    db_session.flush()

    body = client.get("/api/audio/teams").json()

    assert body == [{"team_id": team, "name": "Test Team"}]


def test_a_person_on_no_team_gets_an_empty_list(app_for, outsider: User) -> None:
    assert app_for(outsider).get("/api/audio/teams").json() == []


def test_the_upload_flow_reads_back_as_analyzing(
    client: TestClient, team: str, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The three calls the upload screen makes, then the one it polls."""
    from autune_audio.config import AudioSettings

    class Broker:
        def send_task(self, *_: object, **__: object) -> None:
            pass

    monkeypatch.setattr("autune_audio.enqueue.current_app", Broker())
    monkeypatch.setattr(
        "autune_audio.router.get_audio_settings",
        lambda: AudioSettings(temp_dir=str(tmp_path / "scratch")),
    )

    meeting_id = client.post(
        "/api/audio/meetings", json={"title": "데모 회의", "team_id": team}
    ).json()["meeting_id"]
    client.post(f"/api/audio/meetings/{meeting_id}/consent", json={"attested": True})
    client.post(
        f"/api/audio/meetings/{meeting_id}/recording",
        files={"file": ("demo.m4a", io.BytesIO(b"fake audio"), "audio/mp4")},
    )

    body = client.get(f"/api/audio/meetings/{meeting_id}").json()
    assert body["status"] == "analyzing"
    assert body["title"] == "데모 회의"
    assert body["original_audio_deleted"] is False
