"""The route that hands a recording to the worker.

Calls the route function directly rather than over HTTP: this repository has no
ASGI test harness for any module, and the routes are plain callables outside the
app. What is worth testing here is not FastAPI's plumbing but the order — stage,
commit, queue — and what each failure leaves on disk, because the thing left
behind is a recording.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import UploadFile
from sqlalchemy.orm import Session

from autune_audio import router as router_module
from autune_audio import service
from autune_audio.config import AudioSettings
from autune_audio.router import upload_recording
from autune_audio.storage import RecordingTooLargeError
from autune_core import Meeting, TeamMember, User
from autune_core.errors import PermissionDeniedError


def upload(data: bytes = b"audio", filename: str = "meeting.m4a") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


class _Scope:
    """Bind the route's own `session_scope` to the test's session.

    The route deliberately opens its own transaction — it has to commit before
    queueing — so a test that wants to see the row has to hand it this one.
    """

    def __init__(self, session: Session, *, flush: bool = True) -> None:
        self._session = session
        self._flush = flush

    def __call__(self) -> _Scope:
        return self

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, *exc: object) -> None:
        if self._flush:
            self._session.flush()


@pytest.fixture
def temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stage into the test's own directory, not the developer's real one."""
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(
        "autune_audio.storage.get_settings", lambda: AudioSettings(temp_dir=str(scratch))
    )
    return scratch


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Every ``process_recording.delay`` the route makes, instead of a broker."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        router_module.process_recording,
        "delay",
        lambda meeting_id, path: sent.append((meeting_id, path)),
    )
    return sent


@pytest.fixture
def member(db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch) -> User:
    """A team member, with the route's own session bound to this test's."""
    user = User(email="member@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()

    monkeypatch.setattr(router_module, "session_scope", _Scope(db_session))
    return user


def staged_files(temp_dir: Path) -> list[Path]:
    return sorted(temp_dir.iterdir()) if temp_dir.is_dir() else []


def test_a_recording_opens_a_meeting_and_queues_the_pipeline(
    db_session: Session, team: str, member: User, temp_dir: Path, queued: list[tuple[str, str]]
) -> None:
    accepted = upload_recording(user=member, file=upload(), team_id=team, title="주간 회의")

    assert accepted.status == "analyzing"
    assert db_session.get(Meeting, accepted.meeting_id) is not None

    ((meeting_id, path),) = queued
    assert meeting_id == accepted.meeting_id
    # The worker is handed the file, not a stream, and the file is still there
    # for it — this is the one write in the module that outlives its caller.
    assert Path(path).exists()
    assert Path(path).suffix == ".m4a"


def test_nothing_is_staged_for_a_team_the_uploader_is_not_in(
    team: str,
    temp_dir: Path,
    queued: list[tuple[str, str]],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure that matters most: a rejected upload must not leave the
    recording on disk while the caller is told no."""
    outsider = User(email="outsider@example.com", display_name="남")
    db_session.add(outsider)
    db_session.flush()

    monkeypatch.setattr(router_module, "session_scope", _Scope(db_session, flush=False))

    with pytest.raises(PermissionDeniedError):
        upload_recording(user=outsider, file=upload(), team_id=team, title="남의 회의")

    assert staged_files(temp_dir) == []
    assert queued == []


def test_the_staged_file_is_deleted_when_queueing_fails(
    team: str, member: User, temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broker that is down leaves a recording nothing will ever adopt. The
    sweep is the net under the failures the route cannot see, not a reason to
    skip the one it is standing in."""

    def broken(meeting_id: str, path: str) -> None:
        raise RuntimeError("the broker is down")

    monkeypatch.setattr(router_module.process_recording, "delay", broken)

    with pytest.raises(RuntimeError, match="the broker is down"):
        upload_recording(user=member, file=upload(), team_id=team, title="주간 회의")

    assert staged_files(temp_dir) == []


def test_a_meeting_whose_task_never_queued_is_marked_failed(
    db_session: Session, team: str, member: User, temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row outlives the request even though nothing will ever process it:
    `session_scope` committed before `delay` was called. The sweep covers the
    staged file; nothing covered the meeting, so it sat in `analyzing` with no
    file and no task — harder to find than the leak the sweep is for."""

    def broken(meeting_id: str, path: str) -> None:
        raise RuntimeError("the broker is down")

    monkeypatch.setattr(router_module.process_recording, "delay", broken)
    monkeypatch.setattr(service, "session_scope", _Scope(db_session))

    with pytest.raises(RuntimeError, match="the broker is down"):
        upload_recording(user=member, file=upload(), team_id=team, title="주간 회의")

    ((meeting,),) = [db_session.query(Meeting).filter(Meeting.team_id == team).all()]
    assert meeting.status == "failed"


def test_marking_it_failed_never_replaces_the_original_error(
    team: str, member: User, temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`abandon_meeting` runs inside an `except`. If it raises, the caller gets a
    database error instead of the reason the request actually failed."""

    def broken(meeting_id: str, path: str) -> None:
        raise RuntimeError("the broker is down")

    def also_broken() -> None:
        raise RuntimeError("and so is the database")

    monkeypatch.setattr(router_module.process_recording, "delay", broken)
    monkeypatch.setattr(service, "session_scope", also_broken)

    with pytest.raises(RuntimeError, match="the broker is down"):
        upload_recording(user=member, file=upload(), team_id=team, title="주간 회의")


def test_an_oversized_recording_is_refused_before_a_meeting_exists(
    db_session: Session,
    team: str,
    member: User,
    temp_dir: Path,
    queued: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage first, so a meeting row never describes an upload that failed.

    The error is not caught here and is not rendered here: ``apps/api`` turns an
    ``AutuneError`` into a response carrying its own 413.
    """
    monkeypatch.setattr(router_module, "MAX_UPLOAD_BYTES", 4)
    before = db_session.query(Meeting).count()

    with pytest.raises(RecordingTooLargeError) as raised:
        upload_recording(
            user=member, file=upload(b"audio" * 100), team_id=team, title="너무 큰 회의"
        )

    assert raised.value.status_code == 413
    assert db_session.query(Meeting).count() == before
    assert staged_files(temp_dir) == []
    assert queued == []
