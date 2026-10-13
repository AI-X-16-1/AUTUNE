"""B's side of a team's deletion by its last member (#1007), on PostgreSQL.

Module A deletes the team: it locks the team's memberships and meetings, runs
every meeting's ``on_meeting_deleted`` hooks, then deletes the meetings and
the ``teams`` row in one transaction. B's part is its hook and its foreign
keys, and three things about them are pinned here:

- B's hook runs in its own session while A still holds those locks. If it
  waited on one of them the request would wait on itself for ever.
- What B put on a person's own calendar is queued for removal in a table
  keyed by the person, so the queue outlives the team.
- Everything else B keeps for the team or its meetings goes with them --
  the queue of project minutes still to take back among them. That is the
  user's answer on #1007: those copies stay in the team's own tools, and
  after the deletion nothing in Autune can reach them.
- "Everything else" is every table of B that names a team (``BY_TEAM``): the
  team's Notion target, its 자료 with their text, its 자료 setting and
  alarms, both cleanup queues, and the records of what its members were sent.
  B has no hook for a team. What takes these rows is each table's foreign key
  to ``teams``, as the migrations made it, so each is checked on a row.

The statements A runs are repeated here as SQL: B may not import A.
Committed data, removed at the end.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Base, Meeting, Team, TeamMember, User, deletion
from autune_extraction import tasks
from autune_extraction.models import (
    ExtActionItem,
    ExtCalendarCleanup,
    ExtCalendarEvent,
    ExtDailyDigest,
    ExtExternalCleanup,
    ExtMaterial,
    ExtMaterialAlarm,
    ExtMaterialChunk,
    ExtMaterialSetting,
    ExtMinutesEvent,
    ExtNotionTarget,
    ExtProject,
    ExtProjectSend,
    ExtProjectSendCleanup,
    ExtWeeklyDigest,
    ExtWorkReport,
)

BY_TEAM = (
    ExtProject,
    ExtMaterial,
    ExtMaterialSetting,
    ExtMaterialAlarm,
    ExtProjectSendCleanup,
    ExtExternalCleanup,
    ExtNotionTarget,
    ExtWeeklyDigest,
    ExtDailyDigest,
    ExtWorkReport,
)
"""Every table of B that names a team. A team's deletion has to leave none of
them a row; ``ext_material_chunks`` names a 자료, not a team, and is checked
beside them."""


@pytest.fixture
def seeded(db_engine: sa.Engine) -> Iterator[dict[str, str]]:
    """A team of one with a meeting B has worked on: an item with its due date
    on the person's calendar, and project minutes sent to the team's Notion
    and to the sender's calendar."""
    with Session(db_engine) as session:
        team = Team(name="지울 팀")
        user = User(email="team-deletion@example.com", display_name="남은 사람")
        session.add_all([team, user])
        session.flush()
        session.add(TeamMember(team_id=team.id, user_id=user.id))
        meeting = Meeting(team_id=team.id, title="회의")
        project = ExtProject(team_id=team.id, name="프로젝트")
        session.add_all([meeting, project])
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
        session.add_all(
            [
                ExtCalendarEvent(
                    action_item_id=item.id,
                    meeting_id=meeting.id,
                    user_id=user.id,
                    event_id="ev_due",
                    synced_due_date=date(2026, 10, 9),
                ),
                ExtProjectSend(
                    meeting_id=meeting.id,
                    project_id=project.id,
                    target="notion",
                    external_id="page_minutes",
                ),
                ExtMinutesEvent(
                    meeting_id=meeting.id,
                    project_id=project.id,
                    user_id=user.id,
                    event_id="ev_minutes",
                ),
            ]
        )
        session.commit()
        ids = {"team": team.id, "user": user.id, "meeting": meeting.id, "project": project.id}
    try:
        yield ids
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == ids["team"]))
            session.execute(sa.delete(User).where(User.id == ids["user"]))
            session.commit()


def left(db_engine: sa.Engine, model: type, *where: sa.ColumnElement[bool]) -> int:
    with Session(db_engine) as session:
        return session.scalar(sa.select(sa.func.count()).select_from(model).where(*where)) or 0


def delete_as_module_a_does(
    db_engine: sa.Engine, team: str, meeting: str
) -> tuple[bool, list[BaseException]]:
    """The team's deletion as ``autune_audio.team_deletion.delete_team`` runs
    it: lock the memberships and the meetings, run the meeting's hooks, delete
    the meeting, delete the team, commit.

    Returns whether the hooks were still waiting after ten seconds -- then
    nothing was deleted -- and what they raised."""
    failure: list[BaseException] = []

    def hooks() -> None:
        try:
            deletion.run_meeting_hooks(meeting)
        except BaseException as caught:
            failure.append(caught)

    with Session(db_engine) as request:
        # What ``autune_audio.team_deletion.delete_team`` holds by the time
        # it runs the hooks.
        request.execute(
            sa.text("SELECT id FROM team_members WHERE team_id = :team FOR UPDATE"), {"team": team}
        )
        request.execute(
            sa.text("SELECT id FROM meetings WHERE team_id = :team FOR NO KEY UPDATE"),
            {"team": team},
        )
        thread = threading.Thread(target=hooks)
        thread.start()
        thread.join(timeout=10)
        waited = thread.is_alive()
        if not waited:
            request.execute(sa.delete(Meeting).where(Meeting.id == meeting))
            request.execute(sa.delete(Team).where(Team.id == team))
        request.commit()  # frees the hook either way, so the thread can end
    thread.join(timeout=10)
    return waited, failure


def test_the_hook_runs_under_the_deletions_locks_and_only_the_calendar_queue_outlives_the_team(
    db_engine: sa.Engine, seeded: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        deletion, "_meeting_hooks", {"extraction": tasks.queue_meeting_calendar_events}
    )
    team, meeting, user = seeded["team"], seeded["meeting"], seeded["user"]

    waited, failure = delete_as_module_a_does(db_engine, team, meeting)

    assert not waited, "B's hook waited on a lock the deletion holds"
    assert failure == []
    # The person's own calendar: both entries queued, by person, and still there.
    with Session(db_engine) as session:
        assert sorted(
            session.scalars(
                sa.select(ExtCalendarCleanup.event_id).where(ExtCalendarCleanup.user_id == user)
            )
        ) == ["ev_due", "ev_minutes"]
    # Everything B kept for the team or the meeting is gone -- with it the
    # queue that could have taken the Notion copy back.
    assert left(db_engine, ExtProjectSendCleanup, ExtProjectSendCleanup.team_id == team) == 0
    assert left(db_engine, ExtProject, ExtProject.team_id == team) == 0
    for model in (ExtActionItem, ExtCalendarEvent, ExtProjectSend, ExtMinutesEvent):
        assert left(db_engine, model, model.meeting_id == meeting) == 0, model.__tablename__


def test_the_hook_had_queued_the_minutes_copy_before_the_team_went(
    db_engine: sa.Engine, seeded: dict[str, str]
) -> None:
    """The row the test above finds gone was there: the hook queues the copy
    in the team's Notion for taking back, as at a meeting's expiry, and it is
    the team's deletion that removes the queue. Until that commits, the
    periodic drain could still take the copy back."""
    tasks.queue_meeting_calendar_events(seeded["meeting"])

    with Session(db_engine) as session:
        assert session.execute(
            sa.select(ExtProjectSendCleanup.target, ExtProjectSendCleanup.external_id).where(
                ExtProjectSendCleanup.team_id == seeded["team"]
            )
        ).all() == [("notion", "page_minutes")]


def test_the_list_of_team_tables_is_every_table_of_b_that_names_a_team() -> None:
    """A table of B that gains a foreign key to ``teams`` has to be added to
    ``BY_TEAM``, and so to the test below: a team's rows are deleted by those
    keys alone, and one left out of the list is one nobody checked."""
    named = {
        table.name
        for table in Base.metadata.tables.values()
        if table.name.startswith("ext_")
        and any(key.column.table.name == "teams" for key in table.foreign_keys)
    }

    assert {model.__tablename__ for model in BY_TEAM} == named


def test_nothing_b_keeps_by_team_outlives_the_team(
    db_engine: sa.Engine, seeded: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One row in every table of ``BY_TEAM``, and a 자료's text: all there
    before the deletion, none after it.

    The person outlives the team, and the records of what they were sent about
    it (``ext_weekly_digests``, ``ext_daily_digests``, ``ext_work_reports``) do
    not: each names the team as well, and a record of a team that is gone says
    nothing anybody will read."""
    monkeypatch.setattr(
        deletion, "_meeting_hooks", {"extraction": tasks.queue_meeting_calendar_events}
    )
    team, meeting, user = seeded["team"], seeded["meeting"], seeded["user"]
    now = datetime.now(UTC)
    with Session(db_engine) as session:
        material = ExtMaterial(
            team_id=team, title="자료", source="upload", expires_at=now + timedelta(days=90)
        )
        session.add(material)
        session.flush()
        material_id = material.id
        session.add_all(
            [
                ExtMaterialChunk(material_id=material_id, position=0, text="자료의 본문"),
                ExtMaterialSetting(team_id=team, keep_until_deleted=True),
                ExtMaterialAlarm(team_id=team, marking="korean_marking"),
                ExtNotionTarget(
                    team_id=team,
                    parent_page_id="page_parent",
                    action_db_id="db_actions",
                    decision_db_id="db_decisions",
                    minutes_db_id="db_minutes",
                ),
                # Not the copy the hook queues (``page_minutes``): an older one.
                ExtProjectSendCleanup(team_id=team, target="notion", external_id="page_older"),
                ExtExternalCleanup(team_id=team, system="notion", external_id="page_item"),
                ExtWeeklyDigest(
                    user_id=user, team_id=team, week_start=date(2026, 10, 5), sent_at=now
                ),
                ExtDailyDigest(user_id=user, team_id=team, day=date(2026, 10, 9), sent_at=now),
                ExtWorkReport(user_id=user, team_id=team, day=date(2026, 10, 9), sent_at=now),
            ]
        )
        session.commit()
    # ``seeded`` made the project; the rest is from here. Each is there to go.
    for model in BY_TEAM:
        assert left(db_engine, model, model.team_id == team) == 1, model.__tablename__
    chunks = left(db_engine, ExtMaterialChunk, ExtMaterialChunk.material_id == material_id)
    assert chunks == 1, ExtMaterialChunk.__tablename__

    waited, failure = delete_as_module_a_does(db_engine, team, meeting)

    assert not waited and failure == []
    for model in BY_TEAM:
        assert left(db_engine, model, model.team_id == team) == 0, model.__tablename__
    chunks = left(db_engine, ExtMaterialChunk, ExtMaterialChunk.material_id == material_id)
    assert chunks == 0, ExtMaterialChunk.__tablename__
    assert left(db_engine, User, User.id == user) == 1
