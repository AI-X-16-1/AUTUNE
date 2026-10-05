"""Cancelling and restarting a transcription (spec 2026-10-02).

A worker that dies leaves its meeting ``analyzing`` with nobody coming; a wrong
upload could not be stopped. These cover the two routes that fix that, the
flags the screen draws them from, and the file each one leaves behind.
"""

from __future__ import annotations

import os
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

    cancelled = service.cancel_transcription(
        db_session, meeting_id=analyzing, user=member, settings=settings
    )

    assert cancelled.meeting.status == "failed"
    assert cancelled.orphan is None  # a live worker owns the file
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


def test_cancelling_a_stalled_job_hands_its_file_to_the_caller(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    """Its owner is gone, so the API takes ownership -- but deletes only once
    the cancel is committed. Deleted first, a failed commit would leave the
    job `running` with nothing to run."""
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    upload = _upload(settings, job.id)

    cancelled = service.cancel_transcription(
        db_session, meeting_id=analyzing, user=member, settings=settings
    )

    assert cancelled.orphan == upload
    assert upload.exists()


def test_the_cancel_route_deletes_a_stalled_jobs_file_after_the_commit(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    upload = _upload(settings, job.id)

    response = client.post(f"/api/audio/meetings/{analyzing}/transcription/cancel")

    assert response.status_code == 200
    assert not upload.exists()


def test_a_cancel_whose_commit_fails_keeps_the_stalled_jobs_file(
    client: TestClient,
    db_session: Session,
    analyzing: str,
    settings: AudioSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The job is still `running` after a failed commit; a later cancel or
    restart needs the file, and the sweep still bounds how long it stays."""
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    upload = _upload(settings, job.id)

    def broken_commit() -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(db_session, "commit", broken_commit)

    with pytest.raises(RuntimeError):
        client.post(f"/api/audio/meetings/{analyzing}/transcription/cancel")

    assert upload.exists()


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


class Enqueued:
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


# --- restart ------------------------------------------------------------------


def test_a_stalled_job_restarts_from_the_same_upload(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    upload = _upload(settings, old.id)

    new = service.restart_transcription(
        db_session, meeting_id=analyzing, user=member, settings=settings
    )

    assert old.status == "superseded"
    assert old.finished_at is not None
    assert new.status == "queued"
    assert new.meeting_id == analyzing
    assert not upload.exists()
    assert (Path(settings.temp_dir) / f"{new.id}.upload").read_bytes() == b"raw audio stand-in"
    assert db_session.get(Meeting, analyzing).status == "analyzing"


def test_a_job_with_a_live_heartbeat_is_not_restarted(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    """Restarting a live run would run one file twice."""
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(seconds=10))
    _upload(settings, old.id)

    with pytest.raises(service.NotStalledError):
        service.restart_transcription(
            db_session, meeting_id=analyzing, user=member, settings=settings
        )


def test_a_meeting_already_complete_is_not_restarted(
    db_session: Session, meeting: str, member: User, settings: AudioSettings
) -> None:
    """A worker that died between mark_complete and mark_published leaves the
    job running and stale, and the meeting delivered."""
    db_session.get(Meeting, meeting).status = "complete"
    old = _job(db_session, meeting, "running", heartbeat_age=timedelta(minutes=10))
    _upload(settings, old.id)

    with pytest.raises(service.NotStalledError):
        service.restart_transcription(
            db_session, meeting_id=meeting, user=member, settings=settings
        )


def test_a_stalled_job_without_its_file_cannot_restart(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))

    with pytest.raises(service.RecordingGoneError):
        service.restart_transcription(
            db_session, meeting_id=analyzing, user=member, settings=settings
        )


def test_an_upload_past_its_six_hours_cannot_restart(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    upload = _upload(settings, old.id)
    then = (datetime.now(tz=UTC) - timedelta(hours=settings.orphan_after_hours + 1)).timestamp()
    os.utime(upload, (then, then))

    with pytest.raises(service.RecordingGoneError):
        service.restart_transcription(
            db_session, meeting_id=analyzing, user=member, settings=settings
        )


def test_a_second_restart_is_refused(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    """A double click: the first restart's new job is queued and live, so the
    second must find it (not the superseded one) and refuse."""
    old = _job(
        db_session,
        analyzing,
        "running",
        age=timedelta(minutes=10),
        heartbeat_age=timedelta(minutes=10),
    )
    _upload(settings, old.id)
    new = service.restart_transcription(
        db_session, meeting_id=analyzing, user=member, settings=settings
    )
    assert service.latest_job(db_session, meeting_id=analyzing) is new

    with pytest.raises(service.NotStalledError):
        service.restart_transcription(
            db_session, meeting_id=analyzing, user=member, settings=settings
        )


def test_a_live_job_older_than_a_dead_one_is_still_the_latest(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    """created_at is the transaction's start, which can precede a wait on the
    meeting lock, so two uploads seconds apart can be stamped out of order."""
    live = _job(
        db_session, analyzing, "running", age=timedelta(seconds=30), heartbeat_age=timedelta()
    )
    _job(db_session, analyzing, "cancelled", age=timedelta(seconds=5))
    _job(db_session, analyzing, "failed", age=timedelta(seconds=1))

    assert service.latest_job(db_session, meeting_id=analyzing) is live
    assert _detail(client, analyzing)["cancellable"] is True


def test_a_recording_that_vanishes_between_the_checks_is_not_restartable(
    settings: AudioSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = Path(settings.temp_dir) / "job.upload"
    path.write_bytes(b"x")  # exists() would say yes; the stat then loses the race

    def vanished(_self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        raise FileNotFoundError(str(path))

    monkeypatch.setattr("autune_audio.service.storage.upload_path", lambda *_a, **_k: path)
    monkeypatch.setattr(Path, "stat", vanished)

    assert not service.recording_restartable("job", settings=settings, now=datetime.now(tz=UTC))


def test_a_recording_that_vanishes_before_the_rename_is_gone_without_its_path(
    db_session: Session,
    analyzing: str,
    member: User,
    settings: AudioSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    _upload(settings, old.id)
    monkeypatch.setattr("autune_audio.service.recording_restartable", lambda *_a, **_k: True)
    # the file is deleted after the check passed: a stalled job's cancel, or the sweep
    (Path(settings.temp_dir) / f"{old.id}.upload").unlink()

    with pytest.raises(service.RecordingGoneError) as raised:
        service.restart_transcription(
            db_session, meeting_id=analyzing, user=member, settings=settings
        )

    assert settings.temp_dir not in str(raised.value)


def test_an_upload_near_the_end_of_its_six_hours_is_not_offered_a_restart(
    db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    """A restart picked at the last second would be swept before it ran."""
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    upload = _upload(settings, job.id)
    then = (
        datetime.now(tz=UTC) - timedelta(hours=settings.orphan_after_hours) + timedelta(minutes=5)
    ).timestamp()
    os.utime(upload, (then, then))

    assert not service.recording_restartable(job.id, settings=settings, now=datetime.now(tz=UTC))


def test_a_restart_of_a_live_job_is_a_409_not_stalled(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(seconds=5))
    _upload(settings, job.id)

    response = client.post(f"/api/audio/meetings/{analyzing}/transcription/restart")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_stalled"


@pytest.mark.parametrize("action", ["cancel", "restart"])
def test_a_non_member_is_refused_by_both_routes(
    db_session: Session,
    analyzing: str,
    outsider: User,
    settings: AudioSettings,
    action: str,
) -> None:
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    _upload(settings, job.id)
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/audio")
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[current_user] = lambda: outsider

    response = TestClient(app).post(f"/api/audio/meetings/{analyzing}/transcription/{action}")

    assert response.status_code == 403


def test_the_restart_route_queues_the_new_job(
    client: TestClient,
    db_session: Session,
    analyzing: str,
    settings: AudioSettings,
    broker: Enqueued,
) -> None:
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    _upload(settings, old.id)

    response = client.post(f"/api/audio/meetings/{analyzing}/transcription/restart")

    assert response.status_code == 202
    assert response.json() == {"meeting_id": analyzing, "status": "analyzing"}
    [(name, [job_id])] = broker.sent
    assert name == "autune.audio.process_recording"
    assert job_id != old.id
    assert db_session.get(TranscriptionJob, job_id).status == "queued"


def test_a_restart_the_broker_refuses_fails_the_meeting_and_deletes_the_file(
    client: TestClient,
    db_session: Session,
    analyzing: str,
    settings: AudioSettings,
    broker: Enqueued,
) -> None:
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    _upload(settings, old.id)
    broker.explode = True

    response = client.post(f"/api/audio/meetings/{analyzing}/transcription/restart")

    assert response.status_code == 500
    assert db_session.get(Meeting, analyzing).status == "failed"
    assert list(Path(settings.temp_dir).iterdir()) == []


def test_the_restart_route_says_the_recording_is_gone(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))

    response = client.post(f"/api/audio/meetings/{analyzing}/transcription/restart")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "recording_gone"


# --- flags --------------------------------------------------------------------


def _detail(client: TestClient, meeting_id: str) -> dict:
    response = client.get(f"/api/audio/meetings/{meeting_id}")
    assert response.status_code == 200
    return response.json()


def test_a_healthy_run_can_be_cancelled_but_not_restarted(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(seconds=5))
    _upload(settings, job.id)

    body = _detail(client, analyzing)

    assert (body["cancellable"], body["stalled"], body["restartable"], body["cancelled"]) == (
        True,
        False,
        False,
        False,
    )


def test_a_stalled_run_with_its_upload_is_restartable(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    _upload(settings, job.id)

    body = _detail(client, analyzing)

    assert (body["stalled"], body["restartable"], body["cancellable"]) == (True, True, True)


def test_a_stalled_run_without_its_upload_is_stalled_but_not_restartable(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))

    body = _detail(client, analyzing)

    assert (body["stalled"], body["restartable"]) == (True, False)


def test_a_cancelled_meeting_says_so(
    client: TestClient, db_session: Session, meeting: str, settings: AudioSettings
) -> None:
    db_session.get(Meeting, meeting).status = "failed"
    _job(db_session, meeting, "cancelled")

    body = _detail(client, meeting)

    assert body["cancelled"] is True
    assert body["cancellable"] is False


def test_a_meeting_that_failed_on_its_own_is_not_called_cancelled(
    client: TestClient, db_session: Session, meeting: str, settings: AudioSettings
) -> None:
    db_session.get(Meeting, meeting).status = "failed"
    _job(db_session, meeting, "failed")

    assert _detail(client, meeting)["cancelled"] is False


def test_a_job_queued_for_long_is_stalled_and_restartable(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    """A message the broker lost leaves a job `queued` with nobody coming.
    After fifteen minutes it is offered a restart; a waiting job restarted
    by mistake only loses its place, because its late message is declined."""
    job = _job(db_session, analyzing, "queued", age=timedelta(minutes=20))
    _upload(settings, job.id)

    body = _detail(client, analyzing)

    assert (body["stalled"], body["restartable"], body["cancellable"]) == (True, True, True)


def test_a_job_queued_briefly_is_not_stalled(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "queued", age=timedelta(minutes=5))
    _upload(settings, job.id)

    body = _detail(client, analyzing)

    assert (body["stalled"], body["cancellable"]) == (False, True)


def test_a_job_queued_for_long_restarts_from_the_same_upload(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    old = _job(db_session, analyzing, "queued", age=timedelta(minutes=20))
    _upload(settings, old.id)

    new = service.restart_transcription(
        db_session, meeting_id=analyzing, user=member, settings=settings
    )

    assert old.status == "superseded"
    assert (Path(settings.temp_dir) / f"{new.id}.upload").exists()


# --- the restart window ---------------------------------------------------------


def _uploaded_ago(settings: AudioSettings, job_id: str, age: timedelta) -> None:
    path = Path(settings.temp_dir) / f"{job_id}.upload"
    path.write_bytes(b"raw audio stand-in")
    then = (datetime.now(tz=UTC) - age).timestamp()
    os.utime(path, (then, then))


def test_an_upload_with_three_hours_left_is_restartable(
    db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    left = timedelta(hours=3, minutes=5)
    _uploaded_ago(settings, job.id, timedelta(hours=settings.orphan_after_hours) - left)

    assert service.recording_restartable(job.id, settings=settings, now=datetime.now(tz=UTC))


def test_an_upload_with_less_than_three_hours_left_is_not_offered_a_restart(
    db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    """The longest meeting the pipeline is sized for (two hours, about 1.27x
    real time) needs about 2.5 h; a restart with less left would be swept
    out from under its worker."""
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    left = timedelta(hours=2, minutes=55)
    _uploaded_ago(settings, job.id, timedelta(hours=settings.orphan_after_hours) - left)

    assert not service.recording_restartable(job.id, settings=settings, now=datetime.now(tz=UTC))


# --- one clock per timestamp ----------------------------------------------------


class _HostClockADayAhead(datetime):
    """This host's clock, a day off the database's."""

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return datetime.now(tz) + timedelta(days=1)


def test_staleness_is_judged_on_the_databases_clock(
    client: TestClient,
    db_session: Session,
    analyzing: str,
    settings: AudioSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`heartbeat_at` is written by the database (`job_guard.beat_with`), so
    it is compared with the database's now: an API host whose clock is off
    must not call a live worker stalled."""
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(seconds=10))
    _upload(settings, job.id)
    monkeypatch.setattr(service, "datetime", _HostClockADayAhead)

    body = _detail(client, analyzing)

    assert body["stalled"] is False


def test_a_claim_stamps_the_heartbeat_with_the_databases_clock(
    db_session: Session, analyzing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job(db_session, analyzing, "queued")
    monkeypatch.setattr(service, "datetime", _HostClockADayAhead)

    service.claim_job(db_session, job_id=job.id)

    db_now = db_session.scalar(sa.select(sa.func.now()))
    assert job.heartbeat_at is not None
    assert abs(job.heartbeat_at - db_now) < timedelta(minutes=1)
