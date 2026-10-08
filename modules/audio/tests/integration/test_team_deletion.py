"""The last member of a team deletes it (#1007), against a real database.

What the owners asked for on #1007, one test or more each: only the one person
left, with the team's name typed; every meeting's ``on_meeting_deleted`` hooks
before any row goes; refused while a job is queued or running or a live
session is open; no recording of the team's jobs left behind; nobody else's
team touched. And what the documents say besides: a raising hook leaves
everything in place, a voice profile is the sweep's to take, and nothing in
the schema would block the ``DELETE`` or survive it.

Read ``conftest.py`` for ``db_session``: migrations once per session -- every
module's, so the foreign keys read below are the whole product's -- and each
test in a transaction that is rolled back.
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_audio import retention, team_deletion
from autune_audio.config import AudioSettings
from autune_audio.live import registry
from autune_audio.models import (
    EMBEDDING_DIM,
    AudMaskingRule,
    AudSpeakerEmbedding,
    AudTeamInvitation,
    TranscriptionJob,
)
from autune_audio.router import router
from autune_core import (
    AutuneError,
    Meeting,
    Participant,
    Team,
    TeamIntegration,
    TeamMember,
    User,
    Utterance,
    deletion,
    get_session,
)
from autune_core.auth import current_user
from autune_core.errors import PrivacyViolationError

NAME = "고객사 A 프로젝트"


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
    row = Team(name=NAME)
    db_session.add(row)
    db_session.flush()
    return row.id


@pytest.fixture
def last(db_session: Session, team: str) -> User:
    """The one person on the team."""
    return person(db_session, "last@example.com", team=team)


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


def delete(client_for, by: User, team: str, name: str | None = NAME):
    body = {} if name is None else {"name": name}
    return client_for(by).request("DELETE", f"/api/audio/teams/{team}", json=body)


def held(
    session: Session, team: str, *, speaker: User | None = None, job: str | None = "done"
) -> str:
    """A meeting that was held: a participant, a line, and one attempt."""
    meeting = Meeting(team_id=team, title="주간 회의", status="complete")
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


def still_whole(session: Session, team: str, meetings: list[str]) -> None:
    """Nothing of the team went."""
    session.expire_all()
    assert session.get(Team, team) is not None
    assert count(session, Meeting, Meeting.id.in_(meetings)) == len(meetings)
    assert count(session, Utterance, Utterance.meeting_id.in_(meetings)) == len(meetings)


# --- what goes ----------------------------------------------------------------


def test_the_last_member_deletes_the_team_and_everything_under_it(
    db_session: Session, client_for, last: User, team: str, settings: AudioSettings
) -> None:
    gone_long_ago = person(db_session, "left-earlier@example.com")
    first = held(db_session, team, speaker=last)
    second = held(db_session, team, speaker=gone_long_ago, job="failed")
    db_session.add_all(
        [
            AudTeamInvitation(
                team_id=team,
                email="invited@example.com",
                token_hash="0" * 64,
                invited_by=last.id,
                expires_at=datetime.now(tz=UTC) + timedelta(days=7),
            ),
            AudMaskingRule(team_id=team, shape="A-#####", category="other", created_by=last.id),
            TeamIntegration(team_id=team, service="slack", secret="not-a-real-token"),
        ]
    )
    other = Team(name="다른 팀")
    db_session.add(other)
    db_session.flush()
    db_session.add(TeamMember(team_id=other.id, user_id=last.id))
    kept = held(db_session, other.id, speaker=last)

    response = delete(client_for, last, team)

    assert response.status_code == 200, response.text
    # The answer is the teams they are still on.
    assert [row["team_id"] for row in response.json()] == [other.id]
    assert db_session.get(Team, team) is None
    for model, column in (
        (TeamMember, TeamMember.team_id),
        (Meeting, Meeting.team_id),
        (AudTeamInvitation, AudTeamInvitation.team_id),
        (AudMaskingRule, AudMaskingRule.team_id),
        (TeamIntegration, TeamIntegration.team_id),
    ):
        assert count(db_session, model, column == team) == 0, model.__tablename__
    for model, column in (
        (Participant, Participant.meeting_id),
        # The line of somebody who left the team earlier goes with the rest.
        (Utterance, Utterance.meeting_id),
        (TranscriptionJob, TranscriptionJob.meeting_id),
    ):
        assert count(db_session, model, column.in_([first, second])) == 0, model.__tablename__
    # The people are not the team's, and neither is another team.
    assert db_session.get(User, last.id) is not None
    assert db_session.get(User, gone_long_ago.id) is not None
    still_whole(db_session, other.id, [kept])


def test_a_team_with_no_meeting_at_all_can_be_deleted(
    db_session: Session, client_for, last: User, team: str
) -> None:
    assert delete(client_for, last, team).status_code == 200
    assert db_session.get(Team, team) is None


def test_the_log_says_who_and_how_much_and_never_the_name(
    db_session: Session, client_for, last: User, team: str, settings: AudioSettings
) -> None:
    held(db_session, team, speaker=last)

    with capture_logs() as logs:
        assert delete(client_for, last, team).status_code == 200

    (entry,) = [log for log in logs if log["event"] == "team_deleted"]
    assert entry == {
        "event": "team_deleted",
        "log_level": "info",
        "team_id": team,
        "user_id": last.id,
        "meetings": 1,
        "recordings": 0,
    }
    assert NAME not in str(logs)


# --- who, and with what name --------------------------------------------------


def test_a_team_somebody_else_is_on_is_not_deleted(
    db_session: Session, client_for, last: User, team: str
) -> None:
    person(db_session, "mate@example.com", team=team)
    meeting = held(db_session, team, speaker=last)

    response = delete(client_for, last, team)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "team_has_other_members"
    still_whole(db_session, team, [meeting])
    assert count(db_session, TeamMember, TeamMember.team_id == team) == 2


def test_somebody_who_is_not_on_the_team_is_refused_like_any_reader(
    db_session: Session, client_for, last: User, team: str
) -> None:
    stranger = person(db_session, "stranger@example.com")
    meeting = held(db_session, team, speaker=last)

    response = delete(client_for, stranger, team)

    assert response.status_code == 403
    still_whole(db_session, team, [meeting])
    assert count(db_session, TeamMember, TeamMember.team_id == team) == 1


@pytest.mark.parametrize(
    "typed", ["고객사 A", "고객사 a 프로젝트", "고객사  A 프로젝트", "Test Team", " "]
)
def test_a_name_that_is_not_the_teams_deletes_nothing(
    db_session: Session, client_for, last: User, team: str, typed: str
) -> None:
    meeting = held(db_session, team, speaker=last)

    with capture_logs() as logs:
        response = delete(client_for, last, team, typed)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "team_name_mismatch"
    # Neither name comes back or is logged: a team's name can name a client.
    assert NAME not in response.text
    assert NAME not in str(logs)
    still_whole(db_session, team, [meeting])


def test_no_name_at_all_deletes_nothing(
    db_session: Session, client_for, last: User, team: str
) -> None:
    meeting = held(db_session, team, speaker=last)

    assert delete(client_for, last, team, None).status_code == 422
    assert delete(client_for, last, team, "").status_code == 422
    still_whole(db_session, team, [meeting])


@pytest.mark.parametrize(
    "typed", [f"  {NAME}\n", unicodedata.normalize("NFD", NAME)], ids=["spaces", "decomposed"]
)
def test_the_name_is_compared_as_a_person_reads_it(
    db_session: Session, client_for, last: User, team: str, typed: str
) -> None:
    assert typed != NAME

    assert delete(client_for, last, team, typed).status_code == 200
    assert db_session.get(Team, team) is None


# --- refused while a meeting is being processed -------------------------------


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_team_with_a_job_in_progress_is_not_deleted(
    db_session: Session, client_for, last: User, team: str, settings: AudioSettings, status: str
) -> None:
    finished = held(db_session, team, speaker=last)
    busy = held(db_session, team, speaker=last, job=status)
    recording = Path(settings.temp_dir) / f"{job_of(db_session, busy)}.upload"
    recording.write_bytes(b"raw audio stand-in")

    with capture_logs() as logs:
        response = delete(client_for, last, team)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "team_meeting_in_progress"
    still_whole(db_session, team, [finished, busy])
    # The recording is its task's to delete, not this request's.
    assert recording.exists()
    (entry,) = [log for log in logs if log["event"] == "team_deletion_refused"]
    assert (entry["transcribing"], entry["live"]) == (1, 0)


@pytest.mark.parametrize("status", ["done", "failed", "superseded", "cancelled"])
def test_a_finished_attempt_does_not_hold_the_team(
    db_session: Session, client_for, last: User, team: str, settings: AudioSettings, status: str
) -> None:
    held(db_session, team, speaker=last, job=status)

    assert delete(client_for, last, team).status_code == 200
    assert db_session.get(Team, team) is None


def test_a_team_with_a_live_session_open_is_not_deleted(
    db_session: Session, client_for, last: User, team: str
) -> None:
    quiet = held(db_session, team, speaker=last)
    live = held(db_session, team, speaker=last, job=None)
    registry.claim(live, object(), user_id=last.id)  # type: ignore[arg-type]

    response = delete(client_for, last, team)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "team_meeting_in_progress"
    still_whole(db_session, team, [quiet, live])

    registry.clear()
    assert delete(client_for, last, team).status_code == 200


def test_another_teams_meeting_in_progress_holds_nothing(
    db_session: Session, client_for, last: User, team: str, settings: AudioSettings
) -> None:
    other = Team(name="다른 팀")
    db_session.add(other)
    db_session.flush()
    theirs = held(db_session, other.id, job="running")
    live = held(db_session, other.id, job=None)
    registry.claim(live, object(), user_id=last.id)  # type: ignore[arg-type]
    held(db_session, team, speaker=last)

    assert delete(client_for, last, team).status_code == 200
    still_whole(db_session, other.id, [theirs, live])


# --- the hooks ----------------------------------------------------------------


def test_every_meetings_hooks_run_before_any_row_goes(
    db_session: Session,
    client_for,
    last: User,
    team: str,
    settings: AudioSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meetings = sorted(held(db_session, team, speaker=last) for _ in range(3))
    seen: list[tuple[str, int, int, bool]] = []

    def hook(meeting_id: str) -> None:
        seen.append(
            (
                meeting_id,
                count(db_session, Meeting, Meeting.team_id == team),
                count(db_session, Utterance, Utterance.meeting_id.in_(meetings)),
                db_session.get(Team, team) is not None,
            )
        )

    monkeypatch.setattr(deletion, "_meeting_hooks", {"elsewhere": hook})

    assert delete(client_for, last, team).status_code == 200

    # Each meeting once, and for the last of them as for the first: every
    # meeting, every line and the team itself were still there.
    assert seen == [(meeting_id, 3, 3, True) for meeting_id in meetings]
    assert db_session.get(Team, team) is None


def test_a_refused_deletion_runs_no_hook(
    db_session: Session, client_for, last: User, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    held(db_session, team, speaker=last)
    held(db_session, team, speaker=last, job="queued")
    seen: list[str] = []
    monkeypatch.setattr(deletion, "_meeting_hooks", {"elsewhere": seen.append})

    assert delete(client_for, last, team).status_code == 409
    assert delete(client_for, last, team, "아닌 이름").status_code == 422
    person(db_session, "mate@example.com", team=team)
    assert delete(client_for, last, team).status_code == 409

    assert seen == []


def test_a_hook_that_raises_leaves_the_team_and_every_meeting_in_place(
    db_session: Session,
    last: User,
    team: str,
    settings: AudioSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meetings = sorted(held(db_session, team, speaker=last) for _ in range(3))
    db_session.add(
        AudTeamInvitation(
            team_id=team,
            email=None,
            token_hash="1" * 64,
            invited_by=last.id,
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )
    )
    db_session.flush()
    ran: list[str] = []

    def breaks_on_the_second(meeting_id: str) -> None:
        ran.append(meeting_id)
        if len(ran) == 2:
            raise RuntimeError("elsewhere")

    monkeypatch.setattr(deletion, "_meeting_hooks", {"elsewhere": breaks_on_the_second})

    # The savepoint stands in for the request's own rollback.
    with pytest.raises(RuntimeError, match="elsewhere"), db_session.begin_nested():
        team_deletion.delete_team(db_session, team_id=team, member=last, name=NAME)

    assert ran == meetings[:2]
    still_whole(db_session, team, meetings)
    assert count(db_session, AudTeamInvitation, AudTeamInvitation.team_id == team) == 1


# --- recordings ---------------------------------------------------------------


def test_no_recording_of_the_teams_jobs_is_left_behind(
    db_session: Session, client_for, last: User, team: str, settings: AudioSettings
) -> None:
    stopped = held(db_session, team, speaker=last, job="failed")
    held(db_session, team, speaker=last, job="done")
    theirs = Path(settings.temp_dir) / f"{job_of(db_session, stopped)}.upload"
    theirs.write_bytes(b"raw audio stand-in")
    other = Team(name="다른 팀")
    db_session.add(other)
    db_session.flush()
    elsewhere = held(db_session, other.id, job="failed")
    not_theirs = Path(settings.temp_dir) / f"{job_of(db_session, elsewhere)}.upload"
    not_theirs.write_bytes(b"raw audio stand-in")

    with capture_logs() as logs:
        assert delete(client_for, last, team).status_code == 200

    assert sorted(path.name for path in Path(settings.temp_dir).iterdir()) == [not_theirs.name]
    (entry,) = [log for log in logs if log["event"] == "team_deleted"]
    assert entry["recordings"] == 1


def test_a_recording_that_will_not_go_keeps_the_team(
    db_session: Session,
    last: User,
    team: str,
    settings: AudioSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = held(db_session, team, speaker=last, job="failed")
    recording = Path(settings.temp_dir) / f"{job_of(db_session, stopped)}.upload"
    recording.write_bytes(b"raw audio stand-in")

    def refuses(path: Path) -> None:
        raise PrivacyViolationError("the recording could not be deleted")

    monkeypatch.setattr("autune_audio.storage.delete_orphan", refuses)

    with pytest.raises(PrivacyViolationError), db_session.begin_nested():
        team_deletion.delete_team(db_session, team_id=team, member=last, name=NAME)

    still_whole(db_session, team, [stopped])
    assert recording.exists()


# --- what is not the team's ---------------------------------------------------


def test_a_voice_profile_is_the_sweeps_to_take_not_this_requests(
    db_session: Session, client_for, last: User, team: str, settings: AudioSettings
) -> None:
    held(db_session, team, speaker=last)
    db_session.add(
        AudSpeakerEmbedding(
            user_id=last.id, vector=[1.0] + [0.0] * (EMBEDDING_DIM - 1), model_version="test"
        )
    )
    db_session.flush()
    profile = AudSpeakerEmbedding.user_id == last.id

    assert delete(client_for, last, team).status_code == 200

    assert count(db_session, AudSpeakerEmbedding, profile) == 1
    # No meeting names them any more, so the next sweep forgets the voice.
    assert retention.forget_idle_profiles(db_session) == 1
    assert count(db_session, AudSpeakerEmbedding, profile) == 0


# --- the schema ---------------------------------------------------------------


def test_nothing_in_any_modules_schema_blocks_or_outlives_a_teams_deletion(
    db_session: Session,
) -> None:
    """Every table of every module, read from the migrated database.

    ``delete_team`` deletes ``meetings`` and ``teams`` rows and relies on the
    database for the rest. A foreign key to either with no ``ON DELETE`` would
    refuse the ``DELETE``; ``SET NULL`` on a team key would leave the row
    behind with nothing to find it by.
    """
    inspector = sa.inspect(db_session.connection())
    to_teams: dict[str, str | None] = {}
    to_meetings: dict[str, str | None] = {}
    for table in inspector.get_table_names():
        for key in inspector.get_foreign_keys(table):
            rule = (key.get("options") or {}).get("ondelete")
            name = f"{table}.{','.join(key['constrained_columns'])}"
            if key["referred_table"] == "teams":
                to_teams[name] = rule
            elif key["referred_table"] == "meetings":
                to_meetings[name] = rule

    # Enough of them that an inspector reading nothing would not pass.
    assert len(to_teams) >= 20 and len(to_meetings) >= 40
    assert {name: rule for name, rule in to_teams.items() if rule != "CASCADE"} == {}
    assert {
        name: rule for name, rule in to_meetings.items() if rule not in ("CASCADE", "SET NULL")
    } == {}
    # A link to another meeting may be cleared; a table's own meeting may not.
    assert sorted(name for name, rule in to_meetings.items() if rule == "SET NULL") == [
        "ctx_briefs.previous_meeting_id",
        "ctx_topic_links.linked_meeting_id",
    ]
