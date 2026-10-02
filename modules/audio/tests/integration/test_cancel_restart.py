"""Cancelling and restarting a transcription (spec 2026-10-02).

A worker that dies leaves its meeting ``analyzing`` with nobody coming; a wrong
upload could not be stopped. These cover the two routes that fix that, the
flags the screen draws them from, and the file each one leaves behind.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio import service
from autune_audio.config import AudioSettings
from autune_audio.models import TranscriptionJob
from autune_audio.router import router
from autune_core import AutuneError, Meeting, TeamMember, User, get_session
from autune_core.auth import current_user
from autune_core.errors import PermissionDeniedError


def _job(
    db_session: Session,
    meeting: str,
    status: str,
    *,
    age: timedelta = timedelta(),
    heartbeat_age: timedelta | None = None,
) -> TranscriptionJob:
    now = datetime.now(tz=UTC)
    row = TranscriptionJob(
        meeting_id=meeting,
        status=status,
        created_at=now - age,
        heartbeat_at=None if heartbeat_age is None else now - heartbeat_age,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_a_job_can_be_cancelled_and_carries_a_heartbeat(db_session: Session, meeting: str) -> None:
    db_session.get(Meeting, meeting).status = "analyzing"
    job = _job(db_session, meeting, "cancelled", heartbeat_age=timedelta(seconds=5))

    db_session.refresh(job)

    assert job.status == "cancelled"
    assert job.heartbeat_at is not None


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
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AudioSettings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = AudioSettings(temp_dir=str(scratch))
    monkeypatch.setattr("autune_audio.router.get_audio_settings", lambda: fake)
    return fake


@pytest.fixture
def analyzing(db_session: Session, meeting: str) -> str:
    db_session.get(Meeting, meeting).status = "analyzing"
    db_session.flush()
    return meeting


def _upload(settings: AudioSettings, job_id: str) -> Path:
    path = Path(settings.temp_dir) / f"{job_id}.upload"
    path.write_bytes(b"raw audio stand-in")
    return path


@pytest.fixture
def client(db_session: Session, member: User) -> TestClient:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/audio")
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[current_user] = lambda: member
    return TestClient(app)


# --- cancel -------------------------------------------------------------------


def test_cancelling_a_running_job_fails_the_meeting_and_leaves_the_file_to_its_worker(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(seconds=5))
    upload = _upload(settings, job.id)

    meeting = service.cancel_transcription(
        db_session, meeting_id=analyzing, user=member, settings=settings
    )

    assert meeting.status == "failed"
    assert job.status == "cancelled"
    assert job.finished_at is not None
    assert upload.exists()  # the live worker's adopt() deletes it


def test_cancelling_a_queued_job_leaves_the_file_to_the_claim(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "queued")
    upload = _upload(settings, job.id)

    service.cancel_transcription(db_session, meeting_id=analyzing, user=member, settings=settings)

    assert job.status == "cancelled"
    assert upload.exists()  # claim_job declines it with owns_file=True


def test_cancelling_a_stalled_job_deletes_the_file_at_once(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    """Its owner is gone; the API takes ownership rather than wait an hour
    for the sweep."""
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    upload = _upload(settings, job.id)

    service.cancel_transcription(db_session, meeting_id=analyzing, user=member, settings=settings)

    assert not upload.exists()


@pytest.mark.parametrize("meeting_status", ["complete", "failed", "scheduled"])
def test_there_is_nothing_to_cancel_outside_analyzing(
    db_session: Session, meeting: str, member: User, settings: AudioSettings, meeting_status: str
) -> None:
    db_session.get(Meeting, meeting).status = meeting_status
    _job(db_session, meeting, "running")

    with pytest.raises(service.NothingToCancelError):
        service.cancel_transcription(db_session, meeting_id=meeting, user=member, settings=settings)


def test_a_second_cancel_is_refused(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running")
    service.cancel_transcription(db_session, meeting_id=analyzing, user=member, settings=settings)

    with pytest.raises(service.NothingToCancelError):
        service.cancel_transcription(
            db_session, meeting_id=analyzing, user=member, settings=settings
        )


def test_an_outsider_cannot_cancel(
    db_session: Session, analyzing: str, outsider: User, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running")

    with pytest.raises(PermissionDeniedError):
        service.cancel_transcription(
            db_session, meeting_id=analyzing, user=outsider, settings=settings
        )


def test_the_cancel_route_answers_with_the_meetings_state(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running")

    response = client.post(f"/api/audio/meetings/{analyzing}/transcription/cancel")

    assert response.status_code == 200
    assert response.json() == {"meeting_id": analyzing, "status": "failed"}


def test_the_cancel_route_says_why_it_refused(
    client: TestClient, db_session: Session, meeting: str, settings: AudioSettings
) -> None:
    response = client.post(f"/api/audio/meetings/{meeting}/transcription/cancel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "nothing_to_cancel"


def test_claiming_a_job_stamps_its_heartbeat(db_session: Session, analyzing: str) -> None:
    job = _job(db_session, analyzing, "queued")

    service.claim_job(db_session, job_id=job.id)

    assert job.status == "running"
    assert job.heartbeat_at is not None


def test_a_job_claimed_after_a_long_wait_is_not_stalled_and_keeps_its_file(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    """The queue wait is not the worker's silence: cancelling right after the
    claim must leave the upload to the live worker."""
    job = _job(db_session, analyzing, "queued", age=timedelta(seconds=settings.stall_after_s * 5))
    upload = _upload(settings, job.id)
    service.claim_job(db_session, job_id=job.id)

    assert not service.is_stalled(job, settings=settings, now=datetime.now(tz=UTC))
    service.cancel_transcription(db_session, meeting_id=analyzing, user=member, settings=settings)

    assert upload.exists()
