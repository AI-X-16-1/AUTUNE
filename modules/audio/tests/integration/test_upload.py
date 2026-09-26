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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio import service
from autune_audio.config import AudioSettings
from autune_audio.models import TranscriptionJob
from autune_audio.router import router
from autune_core import AutuneError, Meeting, Team, TeamMember, User, get_session
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


def test_a_created_meeting_expires_after_the_teams_retention_window(
    db_session: Session, team: str, member: User
) -> None:
    """Nothing else in the repository writes ``expires_at`` (#206).

    Module D reads ``expires_at IS NULL`` as "never expires", so a meeting
    created without it is one the retention sweep and every retention-aware
    read ignore for good. The window is the team's, resolved when the meeting
    is opened: a team that later shortens its retention does not retroactively
    un-record what was agreed.

    A range, not ``.days == 29``: the clock can tick zero or several times
    between the call and the assertion, and #209 had exactly that test go
    flaky on a coarse-resolution clock.
    """
    db_session.get(Team, team).retention_days = 30
    db_session.flush()

    before = datetime.now(tz=UTC)
    meeting = service.create_meeting(db_session, owner=member, title="회의", team_id=team)
    after = datetime.now(tz=UTC)

    assert meeting.expires_at is not None
    assert before + timedelta(days=30) <= meeting.expires_at <= after + timedelta(days=30)


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

    job = service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)

    assert job.meeting.status == "analyzing"
    assert job.status == "queued"
    assert job.id.startswith("job_")


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
    first = service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)
    service.mark_failed(db_session, job_id=first.id)

    retried = service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)

    assert retried.meeting.status == "analyzing"
    assert retried.id != first.id


def test_a_second_attempt_supersedes_a_stale_one(
    db_session: Session, team: str, member: User
) -> None:
    """The race #275 was opened over, closed at the claim.

    A ``failed`` meeting accepts a new recording. If the first attempt's task
    then turns up late -- a purged queue restored, a worker back from the
    dead -- it must not run against the second attempt's meeting, and it must
    not fail that meeting when it dies. Each attempt is its own row; the old
    one is marked ``superseded`` here so the worker can decline it at the door.
    """
    meeting = service.create_meeting(db_session, owner=member, title="회의", team_id=team)
    first = service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)
    meeting.status = "failed"  # a decoder that fell over, without the job ever reporting
    db_session.flush()

    second = service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)

    assert db_session.get(TranscriptionJob, first.id).status == "superseded"
    assert second.status == "queued"
    # The late first attempt: declined, and the meeting stays the second's.
    claim = service.claim_job(db_session, job_id=first.id)
    assert claim.run is False and claim.owns_file is True
    service.mark_failed(db_session, job_id=first.id)
    assert db_session.get(Meeting, meeting.id).status == "analyzing"


def test_a_redelivery_while_the_first_run_is_going_leaves_its_file_alone(
    db_session: Session, team: str, member: User
) -> None:
    """``acks_late`` plus Redis' one-hour visibility timeout: a long meeting
    is redelivered while the first delivery is still decoding it. The first
    owns the file and deletes it in its ``finally``; the second must not."""
    meeting = service.create_meeting(db_session, owner=member, title="회의", team_id=team)
    job = service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)

    first = service.claim_job(db_session, job_id=job.id)
    second = service.claim_job(db_session, job_id=job.id)

    assert first.run is True
    assert second.run is False and second.owns_file is False


def test_claiming_a_job_nobody_queued_is_loud(db_session: Session) -> None:
    with pytest.raises(NotFoundError):
        service.claim_job(db_session, job_id="job_nope")


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
    job = service.start_transcription(db_session, meeting_id=meeting.id, uploader=member)

    service.mark_failed(db_session, job_id=job.id)

    assert db_session.get(Meeting, meeting.id).status == "failed"
    assert job.status == "failed"
    assert job.finished_at is not None


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
    (job_id,) = args
    assert str(job_id).startswith("job_"), "the payload carries the job id and nothing else"
    assert db_session.get(TranscriptionJob, job_id).meeting_id == meeting_id
    assert [p.name for p in temp_dir.iterdir()] == [f"{job_id}.upload"]
    assert (temp_dir / f"{job_id}.upload").read_bytes() == b"fake audio"
    assert db_session.get(Meeting, meeting_id).status == "analyzing"


def test_the_payload_never_carries_a_path(
    client: TestClient, team: str, broker: Enqueued, temp_dir: Path
) -> None:
    """privacy.md section 1, Forbidden, third line -- checked on the message
    that actually leaves the process, since that is where Celery would write
    it to the broker and to its failure output."""
    meeting_id = _create(client, team)

    client.post(
        f"/api/audio/meetings/{meeting_id}/recording",
        files={"file": ("standup.m4a", io.BytesIO(b"fake audio"), "audio/mp4")},
    )

    _, args = broker.sent[0]
    for arg in args:
        assert "/" not in str(arg) and str(temp_dir) not in str(arg)


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
    (job,) = db_session.scalars(
        sa.select(TranscriptionJob).where(TranscriptionJob.meeting_id == meeting_id)
    )
    assert job.status == "failed"


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


def test_the_clients_filename_never_reaches_the_disk(
    client: TestClient, team: str, broker: Enqueued, temp_dir: Path
) -> None:
    """``"a." + "x" * 300`` as a filename used to take the whole body to disk
    and then die in ``NamedTemporaryFile`` on NAME_MAX (#209 review).

    The file is named after the job now and the client's name is not read at
    all: ffmpeg sniffs the container from the bytes, and a filename is one
    more thing a person typed that could carry a meeting's title.
    """
    meeting_id = _create(client, team)

    response = client.post(
        f"/api/audio/meetings/{meeting_id}/recording",
        files={"file": ("a." + "x" * 300, io.BytesIO(b"fake audio"), "audio/mp4")},
    )

    assert response.status_code == 202
    (job_id,) = broker.sent[0][1]
    assert [p.name for p in temp_dir.iterdir()] == [f"{job_id}.upload"]


def test_a_staging_failure_leaves_the_meeting_untouched(
    client: TestClient,
    team: str,
    broker: Enqueued,
    temp_dir: Path,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disk that will not take the bytes is not the meeting's fault.

    Before the claim there is nothing to release: the meeting is still
    ``scheduled`` and must stay that way, so the person can try again. Only a
    failure *after* the claim — the enqueue — may move it to ``failed``. The
    first version of this route did not tell the two apart and would have
    failed a meeting over a full disk.
    """
    import autune_audio.router as router_module

    def refuse(*_: object, **__: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(router_module, "handover", refuse)
    meeting_id = _create(client, team)

    with pytest.raises(OSError):
        client.post(
            f"/api/audio/meetings/{meeting_id}/recording",
            files={"file": ("standup.m4a", io.BytesIO(b"fake audio"), "audio/mp4")},
        )

    assert db_session.get(Meeting, meeting_id).status == "scheduled"
    assert broker.sent == []
