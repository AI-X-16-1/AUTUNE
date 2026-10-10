"""``calendar_writes.queue_departed_lines`` on PostgreSQL (#937): the lines on
the calendar of somebody who left the meeting's team are queued for the drain,
and a member's stay. The unit tests run on SQLite; this runs the correlated
membership check and the ``ON CONFLICT`` insert where they ship."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from autune_core import Meeting, Team, TeamMember, User, session_scope
from autune_gap import calendar_writes
from autune_gap.models import GapAgendaCleanup, GapAgendaEvent


@pytest.fixture
def seeded(db_engine: object) -> Iterator[dict[str, str]]:  # db_engine ensures migrations ran
    tag = uuid4().hex[:8]
    ids = {"stayed": f"usr_stay_{tag}", "left": f"usr_left_{tag}", "meeting": f"mtg_{tag}"}
    with session_scope() as s:
        team = Team(name=f"gap-departed-{tag}")
        s.add(team)
        s.flush()
        ids["team"] = team.id
        for key in ("stayed", "left"):
            s.add(User(id=ids[key], email=f"{ids[key]}@example.com", display_name=key))
        s.flush()
        s.add(TeamMember(team_id=team.id, user_id=ids["stayed"]))
        s.add(Meeting(id=ids["meeting"], team_id=team.id, title="주간 회의"))
        s.flush()
        for key in ("stayed", "left"):
            s.add(
                GapAgendaEvent(
                    meeting_id=ids["meeting"],
                    gap_id=f"gap_{tag}",
                    user_id=ids[key],
                    calendar_id="primary",
                    event_id=f"evt_{key}_{tag}",
                )
            )
    yield ids
    with session_scope() as s:
        s.execute(delete(Meeting).where(Meeting.id == ids["meeting"]))
        s.execute(delete(User).where(User.id.in_([ids["stayed"], ids["left"]])))
        s.execute(delete(Team).where(Team.id == ids["team"]))


def test_only_the_departed_persons_lines_are_queued(seeded: dict[str, str]) -> None:
    calendar_writes.queue_departed_lines()
    calendar_writes.queue_departed_lines()  # safe to run twice

    with session_scope() as s:
        kept = set(
            s.scalars(
                select(GapAgendaEvent.user_id).where(GapAgendaEvent.meeting_id == seeded["meeting"])
            )
        )
        queued = set(
            s.scalars(
                select(GapAgendaCleanup.user_id).where(
                    GapAgendaCleanup.user_id.in_([seeded["stayed"], seeded["left"]])
                )
            )
        )
    assert kept == {seeded["stayed"]}
    assert queued == {seeded["left"]}
