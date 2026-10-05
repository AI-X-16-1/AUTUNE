"""``ext_project_refresh_owed`` on PostgreSQL (#787 review).

What SQLite cannot show: the PostgreSQL spelling of "record once, however
often", and that the record goes with its meeting -- a deleted meeting's
copies are retracted through ``ext_project_send_cleanup`` instead, and a
refresh owed for it would have nothing left to rewrite.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Team
from autune_extraction import project_send
from autune_extraction.models import ExtProjectRefreshOwed


def _meeting(session: Session) -> Meeting:
    team = Team(name="팀")
    session.add(team)
    session.flush()
    meeting = Meeting(team_id=team.id, title="회의")
    session.add(meeting)
    session.flush()
    return meeting


def test_a_meeting_is_owed_once_and_keeps_its_count(db_session: Session) -> None:
    meeting = _meeting(db_session)

    assert project_send.owe_refresh(db_session, [meeting.id]) == [meeting.id]
    owed = db_session.get(ExtProjectRefreshOwed, meeting.id)
    assert owed is not None and owed.attempts == 0 and owed.created_at is not None
    owed.attempts = 3
    db_session.flush()

    project_send.owe_refresh(db_session, [meeting.id, meeting.id])

    db_session.expire_all()
    rows = db_session.scalars(sa.select(ExtProjectRefreshOwed)).all()
    assert [(r.meeting_id, r.attempts) for r in rows if r.meeting_id == meeting.id] == [
        (meeting.id, 3)
    ]


def test_what_is_owed_goes_with_the_meeting(db_session: Session) -> None:
    meeting = _meeting(db_session)
    meeting_id = meeting.id
    project_send.owe_refresh(db_session, [meeting_id])

    db_session.execute(sa.delete(Meeting).where(Meeting.id == meeting_id))
    db_session.expire_all()

    assert db_session.get(ExtProjectRefreshOwed, meeting_id) is None


def test_settling_removes_the_record_and_is_safe_with_none(db_session: Session) -> None:
    meeting = _meeting(db_session)
    project_send.owe_refresh(db_session, [meeting.id])

    project_send.settle_refresh(db_session, meeting.id)
    project_send.settle_refresh(db_session, meeting.id)

    assert db_session.get(ExtProjectRefreshOwed, meeting.id) is None
