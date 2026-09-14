"""Two runs recording one meeting's ambiguous agreements at once, on PostgreSQL.

Raised on #153. The task is ``acks_late``, so a worker that dies mid-run gets
its message redelivered while the first run may still be inside its
transaction. SQLite cannot show this: it has one writer, and the unit suite
can only simulate the interleaving. Here there are two real transactions, and
the second is held on the first's uncommitted row before the first commits.

The data is committed, because both transactions have to see it, and removed
at the end by deleting the team (everything below it cascades).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.enums import UtteranceKind
from autune_core import Meeting, Team, Utterance
from autune_extraction import service
from autune_extraction.decisions import ClassifiedUtterance
from autune_extraction.models import ExtConfirmation


@pytest.fixture
def meeting(db_engine: sa.Engine) -> Iterator[tuple[str, str]]:
    with Session(db_engine) as session:
        team = Team(name="팀")
        session.add(team)
        session.flush()
        meeting = Meeting(team_id=team.id, title="회의")
        session.add(meeting)
        session.flush()
        utterance = Utterance(
            meeting_id=meeting.id, speaker_label="S1", start_sec=0.0, end_sec=1.0, text="네"
        )
        session.add(utterance)
        session.commit()
        team_id, ids = team.id, (meeting.id, utterance.id)
    try:
        yield ids
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == team_id))
            session.commit()


def ambiguous(utterance_id: str) -> list[ClassifiedUtterance]:
    return [
        ClassifiedUtterance(
            id=utterance_id, kind=UtteranceKind.AMBIGUOUS, confidence=0.8, text="..."
        )
    ]


def waiting_on_a_lock(engine: sa.Engine, pid: int) -> bool:
    with engine.connect() as probe:
        return bool(
            probe.execute(
                sa.text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            ).scalar()
        )


def test_a_second_run_waits_for_the_first_and_then_adds_nothing(
    db_engine: sa.Engine, meeting: tuple[str, str]
) -> None:
    meeting_id, utterance_id = meeting
    errors: list[BaseException] = []

    first = Session(db_engine)
    service.record_ambiguous_agreements(
        first, meeting_id=meeting_id, classified=ambiguous(utterance_id)
    )  # inserted, not committed

    second = Session(db_engine)
    second_pid = second.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one()

    def second_run() -> None:
        try:
            service.record_ambiguous_agreements(
                second, meeting_id=meeting_id, classified=ambiguous(utterance_id)
            )
            second.commit()
        except BaseException as caught:  # surfaced to the test thread below
            second.rollback()
            errors.append(caught)

    thread = threading.Thread(target=second_run)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not waiting_on_a_lock(db_engine, second_pid):
            assert thread.is_alive(), "the second run finished without waiting on the first"
            assert time.monotonic() < deadline, "the second run never reached the first's row"
            time.sleep(0.05)
        first.commit()
        thread.join(timeout=10)
    finally:
        first.close()
        second.close()

    assert not thread.is_alive()
    assert errors == []
    with Session(db_engine) as check:
        rows = check.scalars(
            sa.select(ExtConfirmation).where(ExtConfirmation.meeting_id == meeting_id)
        ).all()
    assert [row.utterance_id for row in rows] == [utterance_id]
