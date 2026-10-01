"""The retention sweep against a real database: what goes, what stays, and in what order.

Read `modules/audio/tests/integration/conftest.py` for `db_session`: it runs
`alembic upgrade heads` once per session and wraps each test in a transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_audio import retention, service
from autune_audio.models import EMBEDDING_DIM, AudSpeakerEmbedding
from autune_core import Meeting, Participant, Team, TeamMember, User, Utterance, deletion

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def make_meeting(
    session: Session,
    team: str,
    *,
    expires_at: datetime | None,
    status: str = "complete",
    started_at: datetime | None = None,
) -> str:
    row = Meeting(
        team_id=team, title="t", status=status, expires_at=expires_at, started_at=started_at
    )
    session.add(row)
    session.flush()
    return row.id


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="retention@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


def profile(user_id: str) -> AudSpeakerEmbedding:
    return AudSpeakerEmbedding(
        user_id=user_id, vector=[1.0] + [0.0] * (EMBEDDING_DIM - 1), model_version="test"
    )


def name_in(session: Session, meeting_id: str, user_id: str) -> None:
    session.add(Participant(meeting_id=meeting_id, user_id=user_id, speaker_label="화자 1"))
    session.flush()


def test_an_expired_meeting_goes_with_everything_under_it(db_session: Session, team: str) -> None:
    gone = make_meeting(db_session, team, expires_at=NOW - timedelta(seconds=1))
    db_session.add(
        Utterance(meeting_id=gone, speaker_label="화자 1", start_sec=0, end_sec=1, text="안녕")
    )
    db_session.flush()

    result = retention.sweep(db_session, now=NOW)

    assert result.meetings == (gone,)
    assert db_session.get(Meeting, gone) is None
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Utterance)) == 0


def test_a_meeting_inside_its_window_stays(db_session: Session, team: str) -> None:
    kept = make_meeting(db_session, team, expires_at=NOW + timedelta(days=1))

    assert retention.sweep(db_session, now=NOW).meetings == ()
    assert db_session.get(Meeting, kept) is not None


def test_a_scheduled_meeting_not_yet_held_is_never_expired(db_session: Session, team: str) -> None:
    """Booked far enough ahead, its creation-time window closes before it happens."""
    ahead = make_meeting(
        db_session,
        team,
        expires_at=NOW - timedelta(days=1),
        status="scheduled",
        started_at=NOW + timedelta(days=3),
    )

    retention.sweep(db_session, now=NOW)

    assert db_session.get(Meeting, ahead) is not None


def test_a_meeting_from_before_206_gets_its_window_from_its_team(
    db_session: Session, team: str
) -> None:
    db_session.get(Team, team).retention_days = 30  # type: ignore[union-attr]
    legacy = make_meeting(db_session, team, expires_at=None)

    result = retention.sweep(db_session, now=NOW)

    # At least this one: a database other suites committed to may hold more.
    assert result.backfilled >= 1
    row = db_session.get(Meeting, legacy)
    db_session.refresh(row)
    assert row is not None and row.expires_at == row.created_at + timedelta(days=30)


def test_a_failing_hook_keeps_the_meeting_for_the_next_run(
    db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = make_meeting(db_session, team, expires_at=NOW - timedelta(days=1))

    def broken(meeting_id: str) -> None:
        raise RuntimeError("something outside the database is still there")

    monkeypatch.setattr(deletion, "_meeting_hooks", {"elsewhere": broken})

    result = retention.sweep(db_session, now=NOW)

    assert result.hooks_failed == (gone,)
    assert result.meetings == ()
    assert db_session.get(Meeting, gone) is not None


def test_a_hook_runs_before_its_meeting_is_deleted(
    db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_meeting(db_session, team, expires_at=NOW - timedelta(days=1))
    seen: list[bool] = []

    def hook(meeting_id: str) -> None:
        seen.append(db_session.get(Meeting, meeting_id) is not None)

    monkeypatch.setattr(deletion, "_meeting_hooks", {"elsewhere": hook})

    retention.sweep(db_session, now=NOW)

    assert seen == [True]


def test_a_profile_goes_with_its_owners_last_meeting(
    db_session: Session, team: str, member: User
) -> None:
    last = make_meeting(db_session, team, expires_at=NOW - timedelta(days=1))
    name_in(db_session, last, member.id)
    db_session.add(profile(member.id))
    db_session.flush()

    result = retention.sweep(db_session, now=NOW)

    assert result.profiles == 1
    assert db_session.scalars(sa.select(AudSpeakerEmbedding)).all() == []


def test_a_profile_stays_while_any_meeting_still_names_its_owner(
    db_session: Session, team: str, member: User
) -> None:
    old = make_meeting(db_session, team, expires_at=NOW - timedelta(days=1))
    recent = make_meeting(db_session, team, expires_at=NOW + timedelta(days=60))
    name_in(db_session, old, member.id)
    name_in(db_session, recent, member.id)
    db_session.add(profile(member.id))
    db_session.flush()

    result = retention.sweep(db_session, now=NOW)

    assert result.meetings == (old,)
    assert result.profiles == 0
    assert len(db_session.scalars(sa.select(AudSpeakerEmbedding)).all()) == 1


def test_a_run_is_bounded(db_session: Session, team: str) -> None:
    for _ in range(3):
        make_meeting(db_session, team, expires_at=NOW - timedelta(days=1))

    assert len(retention.sweep(db_session, now=NOW, batch=2).meetings) == 2
    assert len(retention.sweep(db_session, now=NOW, batch=2).meetings) == 1


@pytest.mark.parametrize("held_by", ["live", "upload"])
def test_a_meeting_booked_further_ahead_than_the_window_survives_being_held(
    db_session: Session, team: str, member: User, held_by: str
) -> None:
    """Review of #581: a 30-day team books a meeting five weeks out. Its
    provisional ``expires_at`` has passed by the time it starts, and the sweep
    must not take it mid-recording."""
    db_session.get(Team, team).retention_days = 30  # type: ignore[union-attr]
    now = datetime.now(tz=UTC)
    booked = service.create_meeting(
        db_session, owner=member, team_id=team, title="t", started_at=now + timedelta(days=35)
    )
    # Five weeks later: the provisional window (created + 30d) closed days ago.
    meeting = db_session.get(Meeting, booked.id)
    assert meeting is not None
    meeting.started_at = now - timedelta(minutes=1)
    meeting.expires_at = now - timedelta(days=5)
    db_session.flush()

    if held_by == "live":
        service.begin_live(db_session, meeting_id=booked.id)
    else:
        service.start_transcription(db_session, meeting_id=booked.id, uploader=member)

    assert retention.sweep(db_session, now=now + timedelta(hours=1)).meetings == ()
    assert meeting.expires_at is not None
    assert meeting.expires_at >= now + timedelta(days=30) - timedelta(minutes=1)


def test_a_reconnect_does_not_restart_the_window(
    db_session: Session, team: str, member: User
) -> None:
    meeting_id = make_meeting(
        db_session, team, expires_at=NOW + timedelta(days=3), status="recording"
    )

    service.begin_live(db_session, meeting_id=meeting_id)

    assert db_session.get(Meeting, meeting_id).expires_at == NOW + timedelta(days=3)  # type: ignore[union-attr]


def test_the_backfill_counts_from_when_the_meeting_was_held(db_session: Session, team: str) -> None:
    held = datetime(2026, 9, 1, tzinfo=UTC)
    legacy = make_meeting(db_session, team, expires_at=None, started_at=held)

    retention.sweep(db_session, now=NOW)

    row = db_session.get(Meeting, legacy)
    db_session.refresh(row)
    assert row is not None and row.expires_at == held + timedelta(days=90)
