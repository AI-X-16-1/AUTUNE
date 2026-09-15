"""Two runs writing one meeting's transcript at once, on PostgreSQL.

Raised on #184. The task is ``acks_late`` and `apps/worker` sets no
``visibility_timeout``, so Redis uses its default of one hour. Processing runs
about 1.27x real time on CPU (transcription 0.73 plus diarization 0.54), so a
meeting longer than about 47 minutes is redelivered while the first run is
still inside its transaction -- and a 45-minute meeting is what this pipeline
is for.

SQLite cannot show this: it has one writer. Here there are two real
transactions, and the second is held on the first's lock before the first
commits.

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

from autune_audio.persistence import persist_transcript
from autune_audio.speakers import Utterance as SpokenUtterance
from autune_core.entities import Meeting, Participant, Team, Utterance

TRANSCRIPT = (
    SpokenUtterance(
        speaker="SPEAKER_00",
        start=0.0,
        end=4.0,
        text="다음 회의는 금요일입니다",
        words=(),
        confidence=0.9,
    ),
    SpokenUtterance(
        speaker="SPEAKER_01",
        start=4.2,
        end=7.0,
        text="네 그때 뵙겠습니다",
        words=(),
        confidence=0.9,
    ),
    SpokenUtterance(
        speaker="SPEAKER_00",
        start=7.2,
        end=9.0,
        text="수고하셨습니다",
        words=(),
        confidence=0.9,
    ),
)


@pytest.fixture
def committed_meeting(db_engine: sa.Engine) -> Iterator[str]:
    with Session(db_engine) as session:
        team = Team(name="팀")
        session.add(team)
        session.flush()
        meeting = Meeting(team_id=team.id, title="회의")
        session.add(meeting)
        session.commit()
        team_id, meeting_id = team.id, meeting.id
    try:
        yield meeting_id
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


def test_a_redelivered_run_waits_and_leaves_one_transcript(
    db_engine: sa.Engine, committed_meeting: str
) -> None:
    """Without the lock this leaves the meeting said twice, and raises nothing.

    Six utterances and four participants for a three-utterance, two-speaker
    recording: every consumer gets the meeting doubled, `participants` grows a
    second row per speaker, and nothing in the pipeline reports a problem. That
    is worse than a failure -- there is no error to find it by.
    """
    errors: list[BaseException] = []

    first = Session(db_engine)
    persist_transcript(
        first,
        meeting_id=committed_meeting,
        utterances=TRANSCRIPT,
        duration_seconds=9.0,
        audio_deleted=True,
    )  # written, not committed

    second = Session(db_engine)
    second_pid = second.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one()

    def redelivered() -> None:
        try:
            persist_transcript(
                second,
                meeting_id=committed_meeting,
                utterances=TRANSCRIPT,
                duration_seconds=9.0,
                audio_deleted=True,
            )
            second.commit()
        except BaseException as caught:  # surfaced to the test thread below
            second.rollback()
            errors.append(caught)

    thread = threading.Thread(target=redelivered)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not waiting_on_a_lock(db_engine, second_pid):
            assert thread.is_alive(), "the second run finished without waiting on the first"
            assert time.monotonic() < deadline, "the second run never reached the first's lock"
            time.sleep(0.05)
        first.commit()
        thread.join(timeout=10)
    finally:
        first.close()
        second.close()

    assert not thread.is_alive()
    assert errors == []
    with Session(db_engine) as check:
        utterances = check.scalars(
            sa.select(Utterance).where(Utterance.meeting_id == committed_meeting)
        ).all()
        participants = check.scalars(
            sa.select(Participant).where(Participant.meeting_id == committed_meeting)
        ).all()
    assert len(utterances) == len(TRANSCRIPT)
    assert sorted(p.speaker_label for p in participants) == ["SPEAKER_00", "SPEAKER_01"]
