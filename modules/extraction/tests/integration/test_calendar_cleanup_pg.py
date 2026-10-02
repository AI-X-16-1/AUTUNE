"""The calendar cleanup queue on PostgreSQL (#588).

The meeting hook copies a meeting's due-date events into ``ext_calendar_cleanup``
before the retention sweep deletes the meeting; the copy has to survive that
deletion (no meeting key) and go with its owner's account (``user_id``
cascades). Committed data, removed at the end.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Team, User
from autune_extraction import tasks
from autune_extraction.models import ExtActionItem, ExtCalendarCleanup, ExtCalendarEvent


@pytest.fixture
def seeded(db_engine: sa.Engine) -> Iterator[dict[str, str]]:
    with Session(db_engine) as session:
        team = Team(name="팀")
        user = User(email="cleanup@example.com", display_name="정리")
        session.add_all([team, user])
        session.flush()
        meeting = Meeting(team_id=team.id, title="회의")
        session.add(meeting)
        session.flush()
        item = ExtActionItem(
            meeting_id=meeting.id,
            description="할 일",
            status="todo",
            confidence=0.9,
            origin="model",
        )
        session.add(item)
        session.flush()
        session.add(
            ExtCalendarEvent(
                action_item_id=item.id,
                meeting_id=meeting.id,
                user_id=user.id,
                event_id="ev_pg",
                synced_due_date=date(2026, 10, 9),
            )
        )
        session.commit()
        ids = {"team": team.id, "user": user.id, "meeting": meeting.id}
    try:
        yield ids
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == ids["team"]))
            session.execute(sa.delete(User).where(User.id == ids["user"]))
            session.commit()


def queue(db_engine: sa.Engine, user_id: str) -> list[str]:
    with Session(db_engine) as session:
        return list(
            session.scalars(
                sa.select(ExtCalendarCleanup.event_id).where(ExtCalendarCleanup.user_id == user_id)
            )
        )


def test_the_queue_outlives_the_meeting_and_goes_with_the_account(
    db_engine: sa.Engine, seeded: dict[str, str]
) -> None:
    tasks.queue_meeting_calendar_events(seeded["meeting"])
    with Session(db_engine) as session:  # the sweep deletes the meeting after its hooks
        session.execute(sa.delete(Meeting).where(Meeting.id == seeded["meeting"]))
        session.commit()

    assert queue(db_engine, seeded["user"]) == ["ev_pg"]

    with Session(db_engine) as session:
        session.execute(sa.delete(User).where(User.id == seeded["user"]))
        session.commit()

    assert queue(db_engine, seeded["user"]) == []
