"""Getting a recording into the system.

Until this existed nothing in the repository created a `meetings` row or called
``process_recording`` — the pipeline had no front door and the only caller was a
test. These cover the door: who may open it, what the meeting looks like on the
way through, and what happens to the file when something fails.

The recording outlives the request on purpose (``storage.handover``), so the
tests that matter most are the ones where the enqueue does not happen. A file
written for a task that was never queued is a recording nobody will ever delete.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio import service
from autune_audio.config import AudioSettings
from autune_audio.router import router
from autune_core import AutuneError, Meeting, TeamMember, User, get_session
from autune_core.auth import current_user
from autune_core.errors import ConflictError, NotFoundError, PermissionDeniedError


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


# --- service ----------------------------------------------------------------


def test_a_member_creates_a_meeting(db_session: Session, team: str, member: User) -> None:
    meeting = service.create_meeting(
        db_session, owner=member, title="검색 개편 우선순위 논의", team_id=team
    )

    assert meeting.team_id == team
    assert meeting.title == "검색 개편 우선순위 논의"
    assert meeting.status == "scheduled"
    assert meeting.source == "file_upload"


def test_creating_a_meeting_for_a_team_you_are_not_on_is_refused(
    db_session: Session, team: str, outsider: User
) -> None:
    """A token says who is asking, not whose meetings they may create."""
    with pytest.raises(PermissionDeniedError):
        service.create_meeting(db_session, owner=outsider, title="남의 회의", team_id=team)


def test_starting_transcription_moves_the_meeting_to_analyzing(
    db_session: Session, team: str, member: User
) -> None:
    meeting = service.create_meeting(db_session, owner=member, title="회의", team_id=team)

    started = service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)

    assert started.status == "analyzing"


def test_a_meeting_already_analyzing_refuses_a_second_recording(
    db_session: Session, team: str, member: User
) -> None:
    """``persist_transcript`` replaces the transcript rather than appending.

    That is what makes the task safe to retry, and it is also why a second
    recording must not be accepted for a meeting already being transcribed: the
    two runs would race and the meeting would end up holding whichever finished
    last, with no record that the other ever existed.
    """
    meeting = service.create_meeting(db_session, owner=member, title="회의", team_id=team)
    service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)

    with pytest.raises(ConflictError):
        service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)


def test_a_failed_meeting_accepts_another_recording(
    db_session: Session, team: str, member: User
) -> None:
    """Failure is the one status that is allowed to go back to analyzing.

    A recording that died in the decoder, or an enqueue that never reached the
    broker, leaves a meeting with no transcript and no task. Refusing the second
    attempt would mean the only way to recover is to make a new meeting, which
    throws away whatever else is already attached to this one.
    """
    meeting = service.create_meeting(db_session, owner=member, title="회의", team_id=team)
    service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)
    service.mark_failed(db_session, meeting_id=meeting.id)

    retried = service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)

    assert retried.status == "analyzing"


def test_a_completed_meeting_refuses_another_recording(
    db_session: Session, team: str, member: User
) -> None:
    """Re-recording a finished meeting would silently replace its transcript.

    ``persist_transcript`` replaces rather than appends, and B, C, D and E have
    already been told about the first one. Recovering from that is a rerun
    conversation (#194), not something an upload should start by accident.
    """
    meeting = service.create_meeting(db_session, owner=member, title="회의", team_id=team)
    meeting.status = "complete"
    db_session.flush()

    with pytest.raises(ConflictError):
        service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)


def test_starting_transcription_on_another_teams_meeting_is_refused(
    db_session: Session, team: str, member: User, outsider: User
) -> None:
    meeting = service.create_meeting(db_session, owner=member, title="회의", team_id=team)

    with pytest.raises(PermissionDeniedError):
        service.start_transcription(db_session, meeting_id=meeting.id, uploader=outsider)


def test_starting_transcription_on_a_meeting_that_does_not_exist(
    db_session: Session, member: User
) -> None:
    with pytest.raises(NotFoundError):
        service.start_transcription(db_session, meeting_id="mtg_nope", uploader=member)


def test_a_failed_meeting_can_be_marked_without_a_reader(
    db_session: Session, team: str, member: User
) -> None:
    """``mark_failed`` is called from the worker, where there is no user.

    It takes a meeting id and nothing else on purpose: a task that has just lost
    its recording must be able to say so without an authorisation check it has
    no one to satisfy.
    """
    meeting = service.create_meeting(db_session, owner=member, title="회의", team_id=team)
    service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)

    service.mark_failed(db_session, meeting_id=meeting.id)

    assert db_session.get(Meeting, meeting.id).status == "failed"


# --- routes -----------------------------------------------------------------


class Enqueued:
    """Stands in for the broker and remembers what was sent to it.

    Not a mock of module A's own function: the assertions below are about the
    task name and arguments that actually leave this process, which is the
    contract between the endpoint and the worker.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, list[object]]] = []
        self.explode = False

    def send_task(self, name: str, args: list[object], **_: object) -> None:
        if self.explode:
            raise RuntimeError("broker is unreachable")
        self.sent.append((name, args))


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> Enqueued:
    fake = Enqueued()
    monkeypatch.setattr("autune_audio.enqueue.current_app", fake)
    return fake


@pytest.fixture
def temp_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Recordings land somewhere this test owns, not in the real scratch dir."""
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(
        "autune_audio.router.get_audio_settings",
        lambda: AudioSettings(temp_dir=str(scratch)),
    )
    return scratch


@pytest.fixture
def app_for(db_session: Session):
    """The audio router on a bare app, wired the way apps/api wires it."""

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


def test_creating_a_meeting_returns_its_id(client: TestClient, team: str) -> None:
    response = client.post("/api/audio/meetings", json={"title": "주간 회의", "team_id": team})

    assert response.status_code == 201
    body = response.json()
    assert body["meeting_id"].startswith("mtg_")
    assert body["status"] == "scheduled"


def test_creating_a_meeting_without_a_token_is_refused(app_for, team: str) -> None:
    response = app_for(None).post(
        "/api/audio/meetings", json={"title": "주간 회의", "team_id": team}
    )

    assert response.status_code == 403


def _create(client: TestClient, team: str) -> str:
    response = client.post("/api/audio/meetings", json={"title": "회의", "team_id": team})
    return response.json()["meeting_id"]


def test_uploading_a_recording_queues_the_task_and_leaves_the_file(
    client: TestClient, team: str, broker: Enqueued, temp_dir: Path, db_session: Session
) -> None:
    """The file is still there when the response goes out, and that is the point.

    The worker is another process. If the endpoint deleted the recording on its
    way out there would be nothing to transcribe, so ``handover`` leaves it and
    ``adopt`` in the task is what deletes it.
    """
    meeting_id = _create(client, team)

    response = client.post(
        f"/api/audio/meetings/{meeting_id}/recording",
        files={"file": ("standup.m4a", io.BytesIO(b"fake audio"), "audio/mp4")},
    )

    assert response.status_code == 202
    assert response.json() == {"meeting_id": meeting_id, "status": "analyzing"}

    name, args = broker.sent[0]
    assert name == "autune.audio.process_recording"
    assert args[0] == meeting_id
    queued = Path(str(args[1]))
    assert queued.exists()
    assert queued.read_bytes() == b"fake audio"
    assert queued.suffix == ".m4a"
    assert db_session.get(Meeting, meeting_id).status == "analyzing"


def test_a_failed_enqueue_deletes_the_recording_and_fails_the_meeting(
    client: TestClient, team: str, broker: Enqueued, temp_dir: Path, db_session: Session
) -> None:
    """Nobody is coming to adopt this file, so the endpoint deletes it.

    The alternative is a recording sitting in the scratch directory with no task
    that will ever collect it, which is the durable copy invariant 11 exists to
    prevent.
    """
    meeting_id = _create(client, team)
    broker.explode = True

    response = client.post(
        f"/api/audio/meetings/{meeting_id}/recording",
        files={"file": ("standup.m4a", io.BytesIO(b"fake audio"), "audio/mp4")},
    )

    assert response.status_code == 500
    assert list(temp_dir.iterdir()) == []
    assert db_session.get(Meeting, meeting_id).status == "failed"


def test_uploading_to_a_meeting_already_analyzing_is_refused(
    client: TestClient, team: str, broker: Enqueued, temp_dir: Path
) -> None:
    meeting_id = _create(client, team)
    upload = {"file": ("standup.m4a", io.BytesIO(b"fake audio"), "audio/mp4")}
    client.post(f"/api/audio/meetings/{meeting_id}/recording", files=upload)

    again = client.post(
        f"/api/audio/meetings/{meeting_id}/recording",
        files={"file": ("standup.m4a", io.BytesIO(b"fake audio"), "audio/mp4")},
    )

    assert again.status_code == 409


def test_a_refused_upload_leaves_no_recording_behind(
    client: TestClient, team: str, broker: Enqueued, temp_dir: Path, app_for, outsider: User
) -> None:
    """403 is decided after the bytes are on disk, so it has to clean up too.

    The check cannot come first: the body is being streamed while the request is
    read, and refusing after the write is the case where a recording is most
    easily orphaned.
    """
    meeting_id = _create(client, team)

    response = app_for(outsider).post(
        f"/api/audio/meetings/{meeting_id}/recording",
        files={"file": ("standup.m4a", io.BytesIO(b"fake audio"), "audio/mp4")},
    )

    assert response.status_code == 403
    assert list(temp_dir.iterdir()) == []
    assert broker.sent == []


def test_an_oversized_recording_is_refused_and_deleted(
    client: TestClient,
    team: str,
    broker: Enqueued,
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("autune_audio.router.MAX_UPLOAD_BYTES", 4)
    meeting_id = _create(client, team)

    response = client.post(
        f"/api/audio/meetings/{meeting_id}/recording",
        files={"file": ("standup.m4a", io.BytesIO(b"much more than four bytes"), "audio/mp4")},
    )

    assert response.status_code == 413
    assert list(temp_dir.iterdir()) == []
    assert broker.sent == []
