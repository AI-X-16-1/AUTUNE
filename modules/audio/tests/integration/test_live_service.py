"""Who may open a live session, and what it does to the meeting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from autune_audio import service
from autune_audio.live import registry
from autune_core import Meeting, TeamMember, User
from autune_core.auth import end_sessions, issue_token
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


def test_a_token_naming_a_deleted_user_is_refused(db_session: Session, meeting: str) -> None:
    with pytest.raises(PermissionDeniedError):
        service.authenticate_live(db_session, token=issue_token("usr_nobody"), meeting_id=meeting)


def test_a_signed_out_token_is_refused(db_session: Session, meeting: str, member: User) -> None:
    """#727: signing out ends the token for the routes, and the socket decoded
    it for itself, so a signed-out or leaked token still opened a recording.
    One function decides now (``autune_core.auth.user_for_token``)."""
    token = issue_token(member.id)
    end_sessions(member)
    db_session.flush()

    with pytest.raises(PermissionDeniedError):
        service.authenticate_live(db_session, token=token, meeting_id=meeting)


def test_a_token_from_after_the_sign_out_is_let_in(
    db_session: Session, meeting: str, member: User
) -> None:
    # A minute ago, not now: a token issued at the very instant of a sign-out
    # is ended with it, and two readings of the clock can be the same value.
    end_sessions(member, now=datetime.now(UTC) - timedelta(minutes=1))
    db_session.flush()

    user = service.authenticate_live(db_session, token=issue_token(member.id), meeting_id=meeting)

    assert user.id == member.id


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


def test_an_upload_is_refused_while_someone_else_s_live_session_is_open(
    db_session: Session, meeting: str, member: User
) -> None:
    db_session.get(Meeting, meeting).status = "recording"
    db_session.flush()
    session = object()
    registry.claim(meeting, session, user_id="user_somebody_else")  # type: ignore[arg-type]
    try:
        with pytest.raises(ConflictError, match="live session"):
            service.start_transcription(db_session, meeting_id=meeting, uploader=member)
    finally:
        registry.release(meeting, session)  # type: ignore[arg-type]
    # The browser's own upload, after ``stop``, once the claim is gone.
    service.start_transcription(db_session, meeting_id=meeting, uploader=member)


def test_the_live_session_s_own_person_can_upload_over_it(
    db_session: Session, meeting: str, member: User
) -> None:
    """The dev server, 2026-10-08: the first live session after a deploy sat in
    the model's warm-up, which reads no message, so the browser's ``stop`` was
    never read; 15 s later it uploaded and was refused 409 by its own claim.
    The claim is there to stop *another* upload landing under a live socket.
    The person who holds it is the one whose recording this is."""
    db_session.get(Meeting, meeting).status = "recording"
    db_session.flush()
    registry.claim(meeting, object(), user_id=member.id)  # type: ignore[arg-type]

    service.start_transcription(db_session, meeting_id=meeting, uploader=member)

    assert not registry.is_open(meeting)
    assert db_session.get(Meeting, meeting).status == "analyzing"
