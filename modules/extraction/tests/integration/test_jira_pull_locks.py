"""The Jira read-back against edits and deletions in flight, on PostgreSQL.

Raised on #548's review: the read-back read the item without a lock, so a board
edit committed during a run was overwritten by the Jira status it read before.
SQLite has one writer and cannot show it. Here the board edit or the deletion
is a real transaction holding the item's row, and the read-back is seen waiting
on it before it commits.

The data is committed, because both transactions have to see it, and removed
at the end by deleting the team (everything below it cascades).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Team
from autune_extraction import jira_sync
from autune_extraction.models import ExtActionItem, ExtExternalRef
from autune_integrations.fakes import FakeJira

SITE = "cloud-1"


@dataclass
class ReadableJira(FakeJira):
    def status_category(self, issue_key: str) -> str | None:
        return self.categories.get(issue_key)


@pytest.fixture
def issue(db_engine: sa.Engine) -> Iterator[tuple[str, str]]:
    """A todo item with an issue Autune last left in ``new``: (team, item)."""
    with Session(db_engine) as session:
        team = Team(name="팀")
        session.add(team)
        session.flush()
        meeting = Meeting(team_id=team.id, title="회의")
        session.add(meeting)
        session.flush()
        item = ExtActionItem(
            meeting_id=meeting.id,
            description="보고서 초안",
            status="todo",
            confidence=0.9,
            origin="model",
        )
        session.add(item)
        session.flush()
        session.add(
            ExtExternalRef(
                action_item_id=item.id,
                system=jira_sync.JIRA,
                meeting_id=meeting.id,
                external_id="KAN-1",
                site=SITE,
                synced_category="new",
            )
        )
        session.commit()
        team_id, item_id = team.id, item.id
    try:
        yield team_id, item_id
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


def pull(session: Session, jira: ReadableJira, team_id: str) -> list[str]:
    """One run in one session, so a test sees it wait; the task gives each issue
    its own transaction (``tasks._pull_jira_team``)."""
    return [
        item_id
        for item_id, key in jira_sync.pull_candidates(session, team_id=team_id, site=SITE)
        if jira_sync.read_back(session, jira, item_id=item_id, key=key, site=SITE)
    ]


def pull_behind(
    engine: sa.Engine, team_id: str, jira: ReadableJira, holder: Session, hold: Callable[[], None]
) -> tuple[list[str], list[BaseException]]:
    """``hold`` runs in ``holder`` uncommitted; the read-back must wait on it,
    and runs to the end once ``holder`` commits."""
    hold()
    moved: list[str] = []
    errors: list[BaseException] = []
    puller = Session(engine)
    pid = puller.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one()

    def run() -> None:
        try:
            moved.extend(pull(puller, jira, team_id))
            puller.commit()
        except BaseException as caught:  # surfaced to the test thread below
            puller.rollback()
            errors.append(caught)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not waiting_on_a_lock(engine, pid):
            assert thread.is_alive(), "the read-back finished without waiting"
            assert time.monotonic() < deadline, "the read-back never reached the held row"
            time.sleep(0.05)
        holder.commit()
        thread.join(timeout=10)
    finally:
        holder.close()
        puller.close()
    assert not thread.is_alive()
    return moved, errors


def test_a_board_edit_in_flight_is_not_overwritten_by_the_read_back(
    db_engine: sa.Engine, issue: tuple[str, str]
) -> None:
    """Jira moved to done while a person moves the item to in progress on the
    board. Unlocked, the read-back read ``todo``, took Jira's move, and wrote
    ``done`` over the edit once it committed. Now it waits and sees both moved."""
    team_id, item_id = issue
    board = Session(db_engine)

    def edit() -> None:
        board.execute(
            sa.update(ExtActionItem).where(ExtActionItem.id == item_id).values(status="in_progress")
        )

    moved, errors = pull_behind(
        db_engine, team_id, ReadableJira(categories={"KAN-1": "done"}), board, edit
    )

    assert errors == []
    assert moved == []
    with Session(db_engine) as check:
        assert check.get(ExtActionItem, item_id).status == "in_progress"  # type: ignore[union-attr]


def test_a_deletion_in_flight_is_skipped_without_a_deadlock(
    db_engine: sa.Engine, issue: tuple[str, str]
) -> None:
    """Deleting the item locks it, then its ref by cascade. The read-back takes
    the same order, waits behind the deletion, and finds nothing left."""
    team_id, item_id = issue
    deleting = Session(db_engine)

    def delete() -> None:
        deleting.execute(sa.delete(ExtActionItem).where(ExtActionItem.id == item_id))

    moved, errors = pull_behind(
        db_engine, team_id, ReadableJira(categories={"KAN-1": "done"}), deleting, delete
    )

    assert errors == []
    assert moved == []


def test_a_ref_with_no_baseline_gets_one_and_the_board_is_kept(
    db_engine: sa.Engine, issue: tuple[str, str]
) -> None:
    team_id, item_id = issue
    with Session(db_engine) as session:
        session.execute(
            sa.update(ExtExternalRef)
            .where(ExtExternalRef.action_item_id == item_id)
            .values(synced_category=None)
        )
        session.execute(
            sa.update(ExtActionItem).where(ExtActionItem.id == item_id).values(status="done")
        )
        session.commit()

    with Session(db_engine) as session:
        moved = pull(session, ReadableJira(categories={"KAN-1": "indeterminate"}), team_id)
        session.commit()

    assert moved == []
    with Session(db_engine) as check:
        assert check.get(ExtActionItem, item_id).status == "done"  # type: ignore[union-attr]
        ref = check.get(ExtExternalRef, (item_id, jira_sync.JIRA))
        assert ref is not None and ref.synced_category == "indeterminate"
