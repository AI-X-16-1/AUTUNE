"""Two uploads claiming one meeting at once, on PostgreSQL.

Raised by @kjfcvx12 on #259. Without a lock both requests read ``scheduled``,
both queue, and the meeting is transcribed and announced twice with different
``utt_`` ids -- which D's lineage cannot follow (#194). With the lock the
second waits on the first and, once the first commits, reads ``analyzing`` and
is refused.

Same shape as ``test_persistence_concurrency``: two real sessions, the second
held on the first's row lock, and ``pg_stat_activity`` as the witness.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_audio import service
from autune_core.entities import Meeting, Team, TeamMember, User
from autune_core.errors import ConflictError


@pytest.fixture
def committed(db_engine: sa.Engine) -> Iterator[tuple[str, str]]:
    with Session(db_engine) as session:
        team = Team(name="팀")
        user = User(email="claim@example.com", display_name="팀원")
        session.add_all([team, user])
        session.flush()
        session.add(TeamMember(team_id=team.id, user_id=user.id))
        meeting = Meeting(team_id=team.id, title="회의")
        session.add(meeting)
        session.commit()
        ids = (meeting.id, user.id)
        team_id = team.id
    try:
        yield ids
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == team_id))
            session.execute(sa.delete(User).where(User.id == ids[1]))
            session.commit()


def waiting_on_a_lock(engine: sa.Engine, pid: int) -> bool:
    with engine.connect() as probe:
        return bool(
            probe.execute(
                sa.text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            ).scalar()
        )


def test_the_second_upload_waits_and_is_refused(
    db_engine: sa.Engine, committed: tuple[str, str]
) -> None:
    meeting_id, user_id = committed
    outcome: list[BaseException | str] = []

    first = Session(db_engine)
    uploader = first.get(User, user_id)
    assert uploader is not None
    service.start_transcription(first, meeting_id=meeting_id, uploader=uploader)  # not committed

    second = Session(db_engine)
    second_pid = second.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one()

    def double_click() -> None:
        try:
            other = second.get(User, user_id)
            assert other is not None
            service.start_transcription(second, meeting_id=meeting_id, uploader=other)
            second.commit()
            outcome.append("queued twice")
        except BaseException as caught:
            second.rollback()
            outcome.append(caught)

    thread = threading.Thread(target=double_click)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not waiting_on_a_lock(db_engine, second_pid):
            assert thread.is_alive(), "the second claim finished without waiting on the first"
            assert time.monotonic() < deadline, "the second claim never reached the first's lock"
            time.sleep(0.05)
        first.commit()
        thread.join(timeout=10)
    finally:
        first.close()
        second.close()

    assert not thread.is_alive()
    (result,) = outcome
    assert isinstance(result, ConflictError), result
