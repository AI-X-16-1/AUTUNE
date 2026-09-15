"""Opening a meeting for an upload.

Two things this has to get right and neither is about the recording: who may
write a meeting into a team, and when the meeting stops being visible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from autune_audio.service import create_meeting
from autune_contracts.enums import TranscriptSource
from autune_core import Team, TeamMember, User
from autune_core.errors import NotFoundError, PermissionDeniedError


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="member@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


def set_retention(session: Session, team_id: str, days: int) -> None:
    row = session.get(Team, team_id)
    assert row is not None
    row.retention_days = days
    session.flush()


@pytest.fixture
def outsider(db_session: Session) -> User:
    user = User(email="outsider@example.com", display_name="남")
    db_session.add(user)
    db_session.flush()
    return user


def test_a_member_opens_a_meeting_in_their_team(
    db_session: Session, team: str, member: User
) -> None:
    meeting = create_meeting(db_session, uploader=member, team_id=team, title="주간 회의")

    assert meeting.team_id == team
    assert meeting.title == "주간 회의"
    assert meeting.source == TranscriptSource.FILE_UPLOAD.value
    assert meeting.status == "analyzing"


def test_a_signed_in_stranger_cannot_open_one(
    db_session: Session, team: str, outsider: User
) -> None:
    """A token proves who is asking, not which teams they may write into. Without
    this check anyone signed in could attach a recording — and the transcript and
    four modules' analysis that follow — to a team they have nothing to do with.
    """
    with pytest.raises(PermissionDeniedError):
        create_meeting(db_session, uploader=outsider, team_id=team, title="남의 회의")


def test_an_unknown_team_is_not_found(db_session: Session, member: User) -> None:
    with pytest.raises(NotFoundError):
        create_meeting(db_session, uploader=member, team_id="team_nope", title="없는 팀")


def test_the_retention_window_is_set_from_the_team(
    db_session: Session, member: User, team: str
) -> None:
    """Nothing else in the repository writes `expires_at`, and module D reads
    NULL as "never expires" — so a meeting created without it is one every
    retention-aware read ignores forever (#206)."""
    set_retention(db_session, team, 30)

    before = datetime.now(tz=UTC)
    meeting = create_meeting(db_session, uploader=member, team_id=team, title="주간 회의")
    after = datetime.now(tz=UTC)

    # Bracketed rather than `.days == 29`, which @lsh2217 reproduced failing one
    # run in three: that form assumed measurable time passed between the call
    # and the assertion, and on a coarse clock in a warm suite it does not.
    assert meeting.expires_at is not None
    assert before + timedelta(days=30) <= meeting.expires_at <= after + timedelta(days=30)


def test_a_later_retention_change_does_not_move_an_existing_meeting(
    db_session: Session, member: User, team: str
) -> None:
    """The window is a promise made to the people in the room at the time. A team
    that lengthens its retention does not reach back into meetings whose speakers
    were told something shorter."""
    set_retention(db_session, team, 30)
    meeting = create_meeting(db_session, uploader=member, team_id=team, title="주간 회의")
    settled = meeting.expires_at

    set_retention(db_session, team, 365)
    db_session.refresh(meeting)

    assert meeting.expires_at == settled


def test_an_uploaded_meeting_has_no_start_time(
    db_session: Session, team: str, member: User
) -> None:
    """The only time this route knows is when the file arrived, and that is not
    when the meeting happened. Module B resolves every relative deadline against
    this column and names the case in `slots.meeting_day`: a Friday meeting
    uploaded on Monday moves every "내일" by three days. `None` makes B keep the
    phrase for a person to read instead of printing a confident wrong date."""
    meeting = create_meeting(db_session, uploader=member, team_id=team, title="주간 회의")

    assert meeting.started_at is None
