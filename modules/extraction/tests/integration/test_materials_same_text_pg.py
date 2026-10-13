"""Two uploads of one text at once keep it once, on PostgreSQL (#817).

The look for an earlier copy is a read and then an insert. A member whose
first upload goes unanswered may send the second while the first is still
working: neither transaction sees the other's uncommitted row, so without the
team's shelf lock both find no copy and both store one. The unit suite cannot
show this -- SQLite has one writer. Here the first upload is held open,
uncommitted, until the second is seen waiting on the lock; once the first
commits, the second must find its row and answer it.

Another team's upload of the same text at the same moment waits on nothing and
is its own material: the lock and the look are both the team's.

The teams are committed, because every transaction has to see them, and removed
at the end (``ext_materials`` cascades with them).
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
from autune_extraction import materials
from autune_extraction.models import MATERIAL_EMBEDDING_DIM, ExtMaterial, ExtMaterialChunk
from autune_extraction.pipeline.embedder import FakeEmbedder

BODY = "출장비 한도\n국내 출장 숙박비는 1박 12만원까지다.\n문의는 총무 담당 010-2345-6789.\n" * 40
"""More than one piece."""


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever the developer's ``.env`` names: these tests are about rows."""
    embedder = FakeEmbedder(dim=MATERIAL_EMBEDDING_DIM)
    monkeypatch.setattr(materials, "get_material_embedder", lambda: embedder)


@pytest.fixture
def teams(db_engine: sa.Engine) -> Iterator[tuple[str, str]]:
    with Session(db_engine) as session:
        made = [Team(name="팀"), Team(name="다른 팀")]
        session.add_all(made)
        session.commit()
        ids = (made[0].id, made[1].id)
    try:
        yield ids
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id.in_(ids)))
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


def kept(engine: sa.Engine, team_id: str) -> list[tuple[str, str]]:
    with Session(engine) as session:
        return [
            (row.id, row.title)
            for row in session.scalars(sa.select(ExtMaterial).where(ExtMaterial.team_id == team_id))
        ]


def test_two_uploads_of_one_text_at_once_keep_it_once(
    db_engine: sa.Engine, teams: tuple[str, str]
) -> None:
    team_id, _ = teams
    first_stored = threading.Event()
    release_first = threading.Event()
    results: dict[str, Any] = {}

    def run() -> None:
        name = threading.current_thread().name
        try:
            with Session(db_engine) as session:
                stored = materials.store_upload(
                    session, team_id, title=name, file_name="limits.txt", data=BODY.encode()
                )
                answer = (stored.row.id, stored.row.title, stored.already_kept)
                if name == "first":
                    # Hold the first upload before its commit -- the window in
                    # which both used to find no copy -- until the second has
                    # either queued behind the lock or, without one, finished.
                    first_stored.set()
                    assert release_first.wait(timeout=10)
                session.commit()
            results[name] = answer
        except Exception as exc:  # noqa: BLE001 -- reported by the assertions below
            results[name] = exc

    first = threading.Thread(target=run, name="first")
    first.start()
    assert first_stored.wait(timeout=10), "the first upload never got in"
    second = threading.Thread(target=run, name="second")
    second.start()
    deadline = time.monotonic() + 10
    while second.is_alive() and not someone_waits_on_the_shelf_lock(db_engine):
        assert time.monotonic() < deadline, "the second upload neither waited nor ran"
        time.sleep(0.05)
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not isinstance(results["first"], Exception), results["first"]
    assert not isinstance(results["second"], Exception), results["second"]
    first_id, _, first_already = results["first"]
    assert first_already is False
    # The second answers the first's row: its id, its title, and that it wrote nothing.
    assert results["second"] == (first_id, "first", True)
    assert kept(db_engine, team_id) == [(first_id, "first")], "one text, one material"
    with Session(db_engine) as session:
        pieces = session.scalar(
            sa.select(sa.func.count())
            .select_from(ExtMaterialChunk)
            .where(ExtMaterialChunk.material_id == first_id)
        )
        assert pieces == len(materials.cut(BODY.replace("010-2345-6789", "***-****-****")))


def test_another_teams_upload_of_the_same_text_neither_waits_nor_is_answered_ours(
    db_engine: sa.Engine, teams: tuple[str, str]
) -> None:
    ours, theirs = teams
    with Session(db_engine) as held:
        mine = materials.store_upload(
            held, ours, title="우리 것", file_name="limits.txt", data=BODY.encode()
        )
        # Our upload is uncommitted and our shelf is locked. Theirs goes through.
        with Session(db_engine) as other:
            other.execute(sa.text("SET LOCAL lock_timeout = '3s'"))
            their_own = materials.store_upload(
                other, theirs, title="그쪽 것", file_name="limits.txt", data=BODY.encode()
            )
            assert their_own.already_kept is False
            assert their_own.row.id != mine.row.id
            other.commit()
        held.commit()

    assert [title for _, title in kept(db_engine, ours)] == ["우리 것"]
    assert [title for _, title in kept(db_engine, theirs)] == ["그쪽 것"]
    with Session(db_engine) as session:
        again = materials.store_upload(
            session, theirs, title="다시", file_name="limits.txt", data=BODY.encode()
        )
        assert (again.already_kept, again.row.title) == (True, "그쪽 것")
