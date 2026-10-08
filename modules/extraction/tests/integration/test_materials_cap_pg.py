"""Two registrations at once do not pass a team's cap, on PostgreSQL (#1016 review).

The cap is a count and then an insert. With one free place, two requests used
to count the same number, both insert and both commit: neither transaction
sees the other's uncommitted row. The unit suite cannot show this -- SQLite
has one writer. Here the first registration is held open, uncommitted, until
the second is seen waiting on the team's shelf lock; once the first commits,
the second must count its row and be refused.

The team is committed, because both transactions have to see it, and removed
at the end (``ext_materials`` cascades with it).
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
from autune_core.errors import ValidationError
from autune_extraction import materials
from autune_extraction.models import ExtMaterial


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


def someone_waits_on_the_shelf_lock(engine: sa.Engine) -> bool:
    with engine.connect() as probe:
        return bool(
            probe.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE wait_event_type = 'Lock' AND wait_event = 'advisory'"
                    " AND datname = current_database()"
                    " AND query LIKE '%pg_advisory_xact_lock%'"
                )
            ).scalar()
        )


def test_two_registrations_at_once_do_not_pass_the_cap(
    db_engine: sa.Engine, team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(materials, "MAX_MATERIALS", 1)
    first_registered = threading.Event()
    release_first = threading.Event()
    results: dict[str, Any] = {}

    def run(file_id: str) -> None:
        name = threading.current_thread().name
        try:
            with Session(db_engine) as session:
                materials.register(
                    session,
                    team_id,
                    title=name,
                    link=f"https://docs.google.com/document/d/{file_id}/edit",
                )
                if name == "first":
                    # Hold the first registration before its commit -- the
                    # window in which both used to count an empty shelf --
                    # until the second has either queued behind the lock or,
                    # without one, finished.
                    first_registered.set()
                    assert release_first.wait(timeout=10)
                session.commit()
            results[name] = "kept"
        except Exception as exc:  # noqa: BLE001 -- reported by the assertions below
            results[name] = exc

    first = threading.Thread(target=run, args=("a" * 20,), name="first")
    first.start()
    assert first_registered.wait(timeout=10), "the first registration never got in"
    second = threading.Thread(target=run, args=("b" * 20,), name="second")
    second.start()
    deadline = time.monotonic() + 10
    while second.is_alive() and not someone_waits_on_the_shelf_lock(db_engine):
        assert time.monotonic() < deadline, "the second registration neither waited nor ran"
        time.sleep(0.05)
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert results["first"] == "kept", results["first"]
    assert isinstance(results["second"], ValidationError), results["second"]
    with Session(db_engine) as session:
        kept = session.scalars(sa.select(ExtMaterial.title).where(ExtMaterial.team_id == team_id))
        assert kept.all() == ["first"], "one place, one material"
