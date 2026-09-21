"""Who may open a live session, and what it does to the meeting."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from autune_audio import service
from autune_core import Meeting, TeamMember, User
from autune_core.auth import issue_token
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


def test_a_member_with_a_valid_token_is_let_in(
    db_session: Session, meeting: str, member: User
) -> None:
    user = service.authenticate_live(db_session, token=issue_token(member.id), meeting_id=meeting)
    assert user.id == member.id


def test_a_bad_token_is_refused(db_session: Session, meeting: str) -> None:
    with pytest.raises(PermissionDeniedError):
        service.authenticate_live(db_session, token="not-a-token", meeting_id=meeting)


def test_an_outsider_is_refused(db_session: Session, meeting: str, outsider: User) -> None:
    with pytest.raises(PermissionDeniedError):
        service.authenticate_live(db_session, token=issue_token(outsider.id), meeting_id=meeting)


def test_a_missing_meeting_is_not_found(db_session: Session, member: User) -> None:
    with pytest.raises(NotFoundError):
        service.authenticate_live(db_session, token=issue_token(member.id), meeting_id="mtg_nope")


def test_beginning_moves_the_meeting_to_recording(db_session: Session, meeting: str) -> None:
    service.begin_live(db_session, meeting_id=meeting)
    assert db_session.get(Meeting, meeting).status == "recording"


def test_beginning_again_on_a_recording_meeting_is_allowed(
    db_session: Session, meeting: str
) -> None:
    """A dropped socket leaves the meeting at `recording`; the browser must be
    able to reconnect."""
    service.begin_live(db_session, meeting_id=meeting)
    service.begin_live(db_session, meeting_id=meeting)
    assert db_session.get(Meeting, meeting).status == "recording"


def test_a_meeting_already_analysed_refuses_a_live_session(
    db_session: Session, meeting: str
) -> None:
    db_session.get(Meeting, meeting).status = "complete"
    db_session.flush()
    with pytest.raises(ConflictError):
        service.begin_live(db_session, meeting_id=meeting)
