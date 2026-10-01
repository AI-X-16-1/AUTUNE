"""Two first-time Notion setups at once make one set of databases, on PostgreSQL (#481).

Before the first setup there is no ``ext_notion_targets`` row, so a row lock has
nothing to hold: both requests read "nothing stored", both make three databases,
and one fails on the primary key, leaving three orphans in Notion. The unit
suite cannot show this -- SQLite has one writer. Here the first setup is held
inside Notion's database creation until the second is seen waiting on the
team's advisory lock; once the first commits, the second must read its row and
reuse its databases.

The team is committed, because both transactions have to see it, and removed at
the end (``ext_notion_targets`` cascades with it).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Team
from autune_core.integrations_config import IntegrationConfig
from autune_extraction import notion_connect, notion_setup
from autune_extraction.models import ExtNotionTarget


@pytest.fixture
def team_id(db_engine: sa.Engine) -> Iterator[str]:
    with Session(db_engine) as session:
        team = Team(name="팀")
        session.add(team)
        session.commit()
        created = team.id
    try:
        yield created
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == created))
            session.commit()


def someone_waits_on_the_setup_lock(engine: sa.Engine) -> bool:
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


class _Client:
    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *exc: Any) -> None:
        pass


def test_two_first_setups_at_once_make_one_set_of_databases(
    db_engine: sa.Engine, team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []
    first_in_notion = threading.Event()
    second_in_notion = threading.Event()
    release_first = threading.Event()

    def create_database(client: Any, *, page_id: str, title: str, **_: Any) -> str:
        if threading.current_thread().name == "second":
            second_in_notion.set()
        elif not first_in_notion.is_set():
            # Hold the first setup inside Notion -- the window in which both
            # used to read "nothing stored" -- until the second has either
            # queued behind the lock or, without one, reached Notion too.
            first_in_notion.set()
            assert release_first.wait(timeout=10)
        created.append(title)
        return f"db-{len(created)}"

    monkeypatch.setattr(
        notion_connect,
        "load_integration",
        lambda _s, team, svc: IntegrationConfig(
            "notion", team, "ntn_token", {"workspace_id": "ws"}
        ),
    )
    monkeypatch.setattr(notion_setup, "notion_client", lambda token: _Client())
    monkeypatch.setattr(notion_setup, "create_database", create_database)
    monkeypatch.setattr(notion_setup, "create_home_page", lambda client, *, page_id: "home")
    monkeypatch.setattr(notion_setup, "retire_status_codes", lambda client, database_id: None)
    monkeypatch.setattr(notion_connect.tasks.backfill_notion, "delay", lambda team: None)

    results: dict[str, Any] = {}

    def run() -> None:
        name = threading.current_thread().name
        try:
            results[name] = notion_connect.set_up(team_id, "page-1")
        except Exception as exc:  # noqa: BLE001 -- reported by the assertions below
            results[name] = exc

    first = threading.Thread(target=run, name="first")
    first.start()
    assert first_in_notion.wait(timeout=10), "the first setup never reached Notion"
    second = threading.Thread(target=run, name="second")
    second.start()
    deadline = time.monotonic() + 10
    while not (someone_waits_on_the_setup_lock(db_engine) or second_in_notion.is_set()):
        assert time.monotonic() < deadline, "the second setup neither waited nor ran"
        time.sleep(0.05)
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert len(created) == 3, "one set of databases, not two"
    assert not isinstance(results["first"], Exception), results["first"]
    assert not isinstance(results["second"], Exception), results["second"]
    assert results["first"]["databases"] == "created"
    assert results["second"]["databases"] == "reused"
    with Session(db_engine) as session:
        row = session.get(ExtNotionTarget, team_id)
        assert row is not None
        assert (row.action_db_id, row.decision_db_id, row.minutes_db_id) == ("db-1", "db-2", "db-3")
