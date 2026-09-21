"""Reading a stored transcript back.

The most sensitive thing this repository stores, so the tests that matter are
the ones about who may read it and what comes back masked.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from autune_audio.persistence import persist_transcript
from autune_audio.service import transcript_for_meeting
from autune_audio.speakers import Utterance as SpokenUtterance
from autune_core import TeamMember, User
from autune_core.errors import NotFoundError, PermissionDeniedError


def spoken(speaker: str, start: float, end: float, text: str) -> SpokenUtterance:
    return SpokenUtterance(
        speaker=speaker, start=start, end=end, text=text, words=(), confidence=0.9
    )


TRANSCRIPT = (
    spoken("SPEAKER_00", 0.0, 4.0, "연락처는 010-****-5678입니다"),
    spoken("SPEAKER_01", 4.2, 7.0, "네 그때 뵙겠습니다"),
)


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


def test_a_member_reads_the_transcript(db_session: Session, meeting: str, member: User) -> None:
    persist_transcript(
        db_session,
        meeting_id=meeting,
        utterances=TRANSCRIPT,
        duration_seconds=7.0,
        audio_deleted=True,
    )

    read = transcript_for_meeting(db_session, meeting_id=meeting, reader=member)

    assert [u.text for u in read] == [u.text for u in TRANSCRIPT]
    assert [u.start for u in read] == [0.0, 4.2]
    assert all(u.id.startswith("utt_") for u in read)


def test_someone_outside_the_team_cannot_read_it(
    db_session: Session, meeting: str, outsider: User
) -> None:
    """A token proves who is asking, not whose meetings they may read. This is
    the check #156 and #189 say the rest of the routes are missing."""
    persist_transcript(
        db_session,
        meeting_id=meeting,
        utterances=TRANSCRIPT,
        duration_seconds=7.0,
        audio_deleted=True,
    )

    with pytest.raises(PermissionDeniedError):
        transcript_for_meeting(db_session, meeting_id=meeting, reader=outsider)


def test_an_unknown_meeting_is_not_found(db_session: Session, member: User) -> None:
    with pytest.raises(NotFoundError):
        transcript_for_meeting(db_session, meeting_id="mtg_nope", reader=member)


def test_a_meeting_still_being_processed_reads_empty(
    db_session: Session, meeting: str, member: User
) -> None:
    """Not an error and not a spinner that never stops. `persist_transcript`
    writes every utterance in one transaction at the end, so "none yet" is what
    a meeting part-way through honestly looks like."""
    assert transcript_for_meeting(db_session, meeting_id=meeting, reader=member) == []


def test_what_comes_back_is_what_the_event_carries(
    db_session: Session, meeting: str, member: User
) -> None:
    """The read and the published event share one builder, so the screen and the
    four consuming modules cannot come to disagree about what was said."""
    from autune_audio.persistence import transcript_payload

    persist_transcript(
        db_session,
        meeting_id=meeting,
        utterances=TRANSCRIPT,
        duration_seconds=7.0,
        audio_deleted=True,
    )

    read = transcript_for_meeting(db_session, meeting_id=meeting, reader=member)
    published = transcript_payload(db_session, meeting_id=meeting).utterances

    assert read == published
