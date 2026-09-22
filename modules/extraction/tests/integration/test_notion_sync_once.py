"""One Notion page per confirmed item, on PostgreSQL (#30).

The unit suite shows the rule on SQLite, which has one writer. Here two real
transactions sync the same item: the second is held on the first's claim until
the first commits, then finds the row and sends nothing. Also: deleting the item
takes its reference with it.

The data is committed, because both transactions have to see it, and removed at
the end by deleting the team (everything below it cascades).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Team
from autune_extraction import service
from autune_extraction.models import ExtActionItem, ExtExternalRef
from autune_integrations.fakes import FakeNotion


@pytest.fixture
def item_id(db_engine: sa.Engine) -> Iterator[str]:
    with Session(db_engine) as session:
        team = Team(name="팀")
        session.add(team)
        session.flush()
        meeting = Meeting(team_id=team.id, title="회의")
        session.add(meeting)
        session.flush()
        item = ExtActionItem(
            meeting_id=meeting.id,
            description="릴리스 노트 정리",
            status="todo",
            confidence=0.9,
            origin="model",
        )
        session.add(item)
        session.commit()
        team_id, action_item_id = team.id, item.id
    try:
        yield action_item_id
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == team_id))
            session.commit()


def waiting_on_a_lock(engine: sa.Engine, pid: int) -> bool:
    with engine.connect() as probe:
        return bool(
            probe.execute(
                sa.text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            ).scalar()
        )


def test_two_syncs_at_once_send_one_page(db_engine: sa.Engine, item_id: str) -> None:
    notion = FakeNotion()
    first = Session(db_engine)
    second = Session(db_engine)
    try:
        service.sync_action_item_to_notion(first, notion, action_item_id=item_id, database_id="db")
        pid = second.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar()
        outcome: dict[str, object] = {}

        def run_second() -> None:
            outcome["ref"] = service.sync_action_item_to_notion(
                second, notion, action_item_id=item_id, database_id="db"
            )
            second.commit()

        worker = threading.Thread(target=run_second)
        worker.start()
        deadline = time.monotonic() + 5
        while not waiting_on_a_lock(db_engine, int(pid or 0)):
            assert time.monotonic() < deadline, "the second sync never waited on the claim"
            time.sleep(0.05)
        first.commit()
        worker.join(timeout=5)

        assert outcome["ref"] is None
        assert len(notion.pages) == 1
    finally:
        first.close()
        second.close()


def test_deleting_the_item_deletes_its_reference(db_engine: sa.Engine, item_id: str) -> None:
    with Session(db_engine) as session:
        service.sync_action_item_to_notion(
            session, FakeNotion(), action_item_id=item_id, database_id="db"
        )
        session.commit()
        session.execute(sa.delete(ExtActionItem).where(ExtActionItem.id == item_id))
        session.commit()

        assert session.get(ExtExternalRef, (item_id, "notion")) is None
