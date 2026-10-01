"""Two Follow-up approvals at once make one item, on PostgreSQL (#574 review).

``add_followup_item`` refuses while the team has an open Follow-up item. Two
approvals landing together would each find none -- neither sees the other's
uncommitted insert -- and each add one. Here the first is held between its check
and its insert until the second has either queued behind the team's lock or,
without one, reached the insert too; the team must end with one item.

The team is committed, because both transactions have to see it, and removed at
the end (its meeting and items cascade).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Team
from autune_extraction import service, tools
from autune_extraction.models import ExtActionItem


@pytest.fixture
def meeting(db_engine: sa.Engine) -> Iterator[tuple[str, str]]:
    with Session(db_engine) as session:
        team = Team(name="팀")
        session.add(team)
        session.flush()
        row = Meeting(team_id=team.id, title="회의")
        session.add(row)
        session.commit()
        ids = (team.id, row.id)
    try:
        yield ids
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == ids[0]))
            session.commit()


def someone_waits_on_the_followup_lock(engine: sa.Engine) -> bool:
    with engine.connect() as probe:
        return bool(
            probe.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE wait_event_type = 'Lock' AND wait_event = 'advisory'"
                    " AND query LIKE '%pg_advisory_xact_lock%'"
                )
            ).scalar()
        )


def test_two_approvals_at_once_make_one_followup_item(
    db_engine: sa.Engine, meeting: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    team_id, meeting_id = meeting
    first_at_insert = threading.Event()
    second_at_insert = threading.Event()
    release_first = threading.Event()
    real_create = service.create_action_item

    def create(session: Session, payload: Any, **kwargs: Any) -> ExtActionItem:
        if threading.current_thread().name == "second":
            second_at_insert.set()
        elif not first_at_insert.is_set():
            first_at_insert.set()
            assert release_first.wait(timeout=10)
        return real_create(session, payload, **kwargs)

    monkeypatch.setattr(tools.service, "create_action_item", create)
    results: dict[str, Any] = {}

    def run() -> None:
        name = threading.current_thread().name
        results[name] = tools.add_followup_item(team_id, meeting_id)

    first = threading.Thread(target=run, name="first")
    first.start()
    assert first_at_insert.wait(timeout=10), "the first approval never reached its insert"
    second = threading.Thread(target=run, name="second")
    second.start()
    deadline = time.monotonic() + 10
    while not (someone_waits_on_the_followup_lock(db_engine) or second_at_insert.is_set()):
        assert time.monotonic() < deadline, "the second approval neither waited nor ran"
        time.sleep(0.05)
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    with Session(db_engine) as session:
        count = session.scalar(
            sa.select(sa.func.count())
            .select_from(ExtActionItem)
            .where(ExtActionItem.meeting_id == meeting_id, ExtActionItem.origin == "followup")
        )
    assert count == 1, "one follow-up item, not two"
    assert results["first"]["ok"] is True
    assert results["second"]["ok"] is False
