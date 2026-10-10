"""A member deletes a meeting (#1161), against a real database.

What the owners answered on #1161, one test or more each: any member of the
meeting's team, with the meeting's title typed as a team's name is typed
(5); refused while it is being transcribed, and -- as ``team_deletion``
refuses a team -- while a live session is open; the same path as expiry and
team deletion, so every ``on_meeting_deleted`` hook before the row goes and a
raising hook leaves the meeting in place; no recording of its jobs left
behind; no other meeting touched.

Read ``conftest.py`` for ``db_session``: migrations once per session, and each
test in a transaction that is rolled back.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_audio import meeting_deletion
from autune_audio.config import AudioSettings
from autune_audio.live import registry
from autune_audio.models import TranscriptionJob
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
from autune_core.errors import PrivacyViolationError

TITLE = "고객사 A 주간 회의"
OTHER = "아닌 이름"


def person(session: Session, email: str, *, team: str | None = None) -> User:
    user = User(email=email, display_name=email.split("@")[0])
    session.add(user)
    session.flush()
    if team is not None:
        session.add(TeamMember(team_id=team, user_id=user.id))
        session.flush()
    return user


@pytest.fixture
def team(db_session: Session) -> str:
    row = Team(name="고객사 A 프로젝트")
    db_session.add(row)
    db_session.flush()
    return row.id


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    return person(db_session, "member@example.com", team=team)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AudioSettings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = AudioSettings(temp_dir=str(scratch))
    monkeypatch.setattr("autune_audio.storage.get_settings", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def no_hooks_but_the_tests_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Other modules' hooks open their own sessions and cannot see this
    test's uncommitted rows; each module tests its own."""
    monkeypatch.setattr(deletion, "_meeting_hooks", {})


@pytest.fixture(autouse=True)
def no_live_claim_left_behind():
    yield
    registry.clear()


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


def delete(client_for, by: User, meeting: str, title: object = TITLE, **body: object):
    sent = body if title is None else {"title": title, **body}
    return client_for(by).request("DELETE", f"/api/audio/meetings/{meeting}", json=sent)


def held(
    session: Session,
    team: str,
    *,
    title: str = TITLE,
    speaker: User | None = None,
    job: str | None = "done",
) -> str:
    """A meeting that was held: a participant, a line, and one attempt."""
    meeting = Meeting(team_id=team, title=title, status="complete")
    session.add(meeting)
    session.flush()
    participant = Participant(
        meeting_id=meeting.id,
        user_id=speaker.id if speaker is not None else None,
        speaker_label="화자 1",
    )
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
    if job is not None:
        session.add(TranscriptionJob(meeting_id=meeting.id, status=job))
    session.flush()
    return meeting.id


def count(session: Session, model: type, *where: sa.ColumnElement[bool]) -> int:
    session.expire_all()
    return session.scalar(sa.select(sa.func.count()).select_from(model).where(*where)) or 0


def job_of(session: Session, meeting: str) -> str:
    return session.scalars(
        sa.select(TranscriptionJob.id).where(TranscriptionJob.meeting_id == meeting)
    ).one()


def still_whole(session: Session, *meetings: str) -> None:
    """Nothing of these meetings went."""
    assert count(session, Meeting, Meeting.id.in_(meetings)) == len(meetings)
    assert count(session, Participant, Participant.meeting_id.in_(meetings)) == len(meetings)
    assert count(session, Utterance, Utterance.meeting_id.in_(meetings)) == len(meetings)


def gone(session: Session, meeting: str) -> None:
    assert count(session, Meeting, Meeting.id == meeting) == 0
    for model in (Participant, Utterance, TranscriptionJob):
        assert count(session, model, model.meeting_id == meeting) == 0, model.__tablename__


# --- what goes ----------------------------------------------------------------


def test_a_member_deletes_a_meeting_and_everything_under_it(
    db_session: Session, client_for, member: User, team: str, settings: AudioSettings
) -> None:
    left_earlier = person(db_session, "left-earlier@example.com")
    meeting = held(db_session, team, speaker=left_earlier)
    beside = held(db_session, team, speaker=member)
    other = Team(name="다른 팀")
    db_session.add(other)
    db_session.flush()
    elsewhere = held(db_session, other.id, speaker=member)

    response = delete(client_for, member, meeting)

    assert response.status_code == 204, response.text
    assert response.content == b""
    # The line of somebody who is no longer on the team goes with the rest.
    gone(db_session, meeting)
    # The same title on the same team is another meeting.
    still_whole(db_session, beside, elsewhere)
    assert db_session.get(Team, team) is not None
    assert count(db_session, TeamMember, TeamMember.team_id == team) == 1
    assert db_session.get(User, left_earlier.id) is not None


def test_any_member_of_the_team_may_delete_it(
    db_session: Session, client_for, member: User, team: str
) -> None:
    """A meeting has no opener on its row; the answer on #1161 is any member."""
    meeting = held(db_session, team, speaker=member)
    mate = person(db_session, "mate@example.com", team=team)

    assert delete(client_for, mate, meeting).status_code == 204
    gone(db_session, meeting)


@pytest.mark.parametrize("job", [None, "done", "failed", "superseded", "cancelled"])
def test_a_meeting_nothing_is_running_for_can_be_deleted(
    db_session: Session, client_for, member: User, team: str, settings: AudioSettings, job: str
) -> None:
    """Transcription that ended, however it ended, or never began."""
    meeting = held(db_session, team, job=job)

    assert delete(client_for, member, meeting).status_code == 204
    gone(db_session, meeting)


def test_the_log_says_who_and_which_meeting_and_never_the_title(
    db_session: Session, client_for, member: User, team: str, settings: AudioSettings
) -> None:
    meeting = held(db_session, team)

    with capture_logs() as logs:
        delete(client_for, member, meeting)

    (entry,) = [log for log in logs if log["event"] == "meeting_deleted"]
    assert entry == {
        "event": "meeting_deleted",
        "log_level": "info",
        "meeting_id": meeting,
        "team_id": team,
        "user_id": member.id,
        "recordings": 0,
    }
    assert TITLE not in str(logs)


# --- who ----------------------------------------------------------------------


@pytest.mark.parametrize("typed", [TITLE, OTHER, None])
def test_somebody_who_is_not_on_the_team_is_refused_like_any_reader(
    db_session: Session, client_for, team: str, typed: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same 403 whatever was typed: a mismatch would say the meeting
    exists and what it is not called."""
    meeting = held(db_session, team)
    stranger = person(db_session, "stranger@example.com")
    seen: list[str] = []
    monkeypatch.setattr(deletion, "_meeting_hooks", {"elsewhere": seen.append})

    response = delete(client_for, stranger, meeting, typed)

    assert response.status_code == (403 if typed is not None else 422)
    if typed is not None:
        assert response.json()["error"]["code"] == "permission_denied"
    still_whole(db_session, meeting)
    assert seen == []


def test_a_meeting_that_is_not_there_is_a_404(
    db_session: Session, client_for, member: User
) -> None:
    response = delete(client_for, member, "mtg_nothing")

    assert response.status_code == 404
    assert response.json()["error"]["details"] == {"resource": "meeting"}


# --- the title, typed ---------------------------------------------------------


@pytest.mark.parametrize("typed", [OTHER, "", "고객사 a 주간 회의", "고객사 A 주간", TITLE + " 2"])
def test_a_title_that_is_not_the_meetings_deletes_nothing(
    db_session: Session, client_for, member: User, team: str, typed: str
) -> None:
    meeting = held(db_session, team)

    with capture_logs() as logs:
        response = delete(client_for, member, meeting, typed)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "meeting_title_mismatch"
    # Neither title is repeated, to the caller or to the log.
    assert TITLE not in response.text
    assert typed == "" or typed not in response.text
    assert TITLE not in str(logs)
    still_whole(db_session, meeting)


@pytest.mark.parametrize("body", [None, 7, ["x"], "가" * 401])
def test_a_body_the_schema_does_not_take_deletes_nothing(
    db_session: Session, client_for, member: User, team: str, body: object
) -> None:
    meeting = held(db_session, team, title="가" * 400)

    response = delete(client_for, member, meeting, body)

    # Refused as a body, by the schema, before it is held against the
    # meeting's title: the service's own 422 carries ``error``, not ``detail``.
    assert response.status_code == 422
    assert "detail" in response.json()
    still_whole(db_session, meeting)
    # The longest title a meeting can have can still be typed.
    assert delete(client_for, member, meeting, "가" * 400).status_code == 204


@pytest.mark.parametrize(
    "typed",
    [f"  {TITLE}\n", unicodedata.normalize("NFD", TITLE), unicodedata.normalize("NFC", TITLE)],
)
def test_the_title_is_compared_as_a_person_reads_it(
    db_session: Session, client_for, member: User, team: str, typed: str
) -> None:
    meeting = held(db_session, team, title=unicodedata.normalize("NFD", f" {TITLE} "))

    assert delete(client_for, member, meeting, typed).status_code == 204


def test_it_is_the_title_the_meeting_has_now(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = held(db_session, team)
    renamed = client_for(member).patch(f"/api/audio/meetings/{meeting}", json={"title": OTHER})
    assert renamed.status_code == 200

    assert delete(client_for, member, meeting, TITLE).status_code == 422
    still_whole(db_session, meeting)
    assert delete(client_for, member, meeting, OTHER).status_code == 204


def test_nothing_else_in_the_body_is_read(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = held(db_session, team)
    beside = held(db_session, team)

    assert delete(client_for, member, meeting, TITLE, meeting_id=beside).status_code == 204
    still_whole(db_session, beside)


# --- while it is being processed ----------------------------------------------


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_meeting_being_transcribed_is_not_deleted(
    db_session: Session, client_for, member: User, team: str, settings: AudioSettings, status: str
) -> None:
    meeting = held(db_session, team, job=status)
    recording = Path(settings.temp_dir) / f"{job_of(db_session, meeting)}.upload"
    recording.write_bytes(b"raw audio stand-in")

    with capture_logs() as logs:
        response = delete(client_for, member, meeting)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "meeting_in_progress"
    still_whole(db_session, meeting)
    # The recording is its task's to delete, not this request's.
    assert recording.exists()
    (entry,) = [log for log in logs if log["event"] == "meeting_deletion_refused"]
    assert entry == {
        "event": "meeting_deletion_refused",
        "log_level": "info",
        "meeting_id": meeting,
        "team_id": team,
        "user_id": member.id,
        "transcribing": 1,
        "live": False,
    }


def test_a_second_attempt_still_running_holds_a_meeting_whose_first_ended(
    db_session: Session, client_for, member: User, team: str, settings: AudioSettings
) -> None:
    meeting = held(db_session, team, job="failed")
    db_session.add(TranscriptionJob(meeting_id=meeting, status="running"))
    db_session.flush()

    assert delete(client_for, member, meeting).status_code == 409
    still_whole(db_session, meeting)


def test_a_meeting_with_a_live_session_open_is_not_deleted(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = held(db_session, team, job=None)
    registry.claim(meeting, object(), user_id=member.id)  # type: ignore[arg-type]

    response = delete(client_for, member, meeting)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "meeting_in_progress"
    still_whole(db_session, meeting)

    registry.clear()
    assert delete(client_for, member, meeting).status_code == 204


def test_another_meeting_in_progress_holds_nothing(
    db_session: Session, client_for, member: User, team: str, settings: AudioSettings
) -> None:
    meeting = held(db_session, team)
    busy = held(db_session, team, job="running")
    live = held(db_session, team, job=None)
    registry.claim(live, object(), user_id=member.id)  # type: ignore[arg-type]

    assert delete(client_for, member, meeting).status_code == 204
    gone(db_session, meeting)
    still_whole(db_session, busy, live)


# --- the hooks ----------------------------------------------------------------


def test_the_hooks_run_for_this_meeting_before_its_row_goes(
    db_session: Session,
    client_for,
    member: User,
    team: str,
    settings: AudioSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meeting = held(db_session, team)
    beside = held(db_session, team)
    seen: list[tuple[str, str, int, int]] = []

    def hook(name: str):
        def run(meeting_id: str) -> None:
            seen.append(
                (
                    name,
                    meeting_id,
                    count(db_session, Meeting, Meeting.id == meeting),
                    count(db_session, Utterance, Utterance.meeting_id == meeting),
                )
            )

        return run

    monkeypatch.setattr(deletion, "_meeting_hooks", {"one": hook("one"), "two": hook("two")})

    assert delete(client_for, member, meeting).status_code == 204

    # Every module's hook, once, for this meeting and no other, with the
    # meeting and its line still there to read.
    assert seen == [("one", meeting, 1, 1), ("two", meeting, 1, 1)]
    gone(db_session, meeting)
    still_whole(db_session, beside)


def test_a_refused_deletion_runs_no_hook(
    db_session: Session,
    client_for,
    member: User,
    team: str,
    settings: AudioSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    busy = held(db_session, team, job="queued")
    quiet = held(db_session, team)
    seen: list[str] = []
    monkeypatch.setattr(deletion, "_meeting_hooks", {"elsewhere": seen.append})

    assert delete(client_for, member, busy).status_code == 409
    assert delete(client_for, member, quiet, OTHER).status_code == 422
    assert delete(client_for, member, "mtg_nothing").status_code == 404

    assert seen == []


def test_a_hook_that_raises_leaves_the_meeting_in_place(
    db_session: Session,
    member: User,
    team: str,
    settings: AudioSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meeting = held(db_session, team, job="failed")
    recording = Path(settings.temp_dir) / f"{job_of(db_session, meeting)}.upload"
    recording.write_bytes(b"raw audio stand-in")

    def breaks(meeting_id: str) -> None:
        raise RuntimeError("elsewhere")

    monkeypatch.setattr(deletion, "_meeting_hooks", {"elsewhere": breaks})

    # The savepoint stands in for the request's own rollback.
    with pytest.raises(RuntimeError, match="elsewhere"), db_session.begin_nested():
        meeting_deletion.delete_meeting(db_session, meeting_id=meeting, member=member, title=TITLE)

    still_whole(db_session, meeting)
    assert count(db_session, TranscriptionJob, TranscriptionJob.meeting_id == meeting) == 1
    assert recording.exists()


# --- recordings ---------------------------------------------------------------


def test_no_recording_of_the_meetings_jobs_is_left_behind(
    db_session: Session, client_for, member: User, team: str, settings: AudioSettings
) -> None:
    meeting = held(db_session, team, job="failed")
    db_session.add(TranscriptionJob(meeting_id=meeting, status="cancelled"))
    db_session.flush()
    theirs = [
        Path(settings.temp_dir) / f"{job_id}.upload"
        for job_id in db_session.scalars(
            sa.select(TranscriptionJob.id).where(TranscriptionJob.meeting_id == meeting)
        )
    ]
    beside = held(db_session, team, job="failed")
    not_theirs = Path(settings.temp_dir) / f"{job_of(db_session, beside)}.upload"
    for path in (*theirs, not_theirs):
        path.write_bytes(b"raw audio stand-in")

    with capture_logs() as logs:
        assert delete(client_for, member, meeting).status_code == 204

    assert sorted(path.name for path in Path(settings.temp_dir).iterdir()) == [not_theirs.name]
    (entry,) = [log for log in logs if log["event"] == "meeting_deleted"]
    assert entry["recordings"] == 2


def test_a_recording_that_will_not_go_keeps_the_meeting(
    db_session: Session,
    member: User,
    team: str,
    settings: AudioSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meeting = held(db_session, team, job="failed")
    recording = Path(settings.temp_dir) / f"{job_of(db_session, meeting)}.upload"
    recording.write_bytes(b"raw audio stand-in")

    def refuses(path: Path) -> None:
        raise PrivacyViolationError("the recording could not be deleted")

    monkeypatch.setattr("autune_audio.storage.delete_orphan", refuses)

    with pytest.raises(PrivacyViolationError), db_session.begin_nested():
        meeting_deletion.delete_meeting(db_session, meeting_id=meeting, member=member, title=TITLE)

    still_whole(db_session, meeting)
    assert recording.exists()
