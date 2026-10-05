"""A summary is not stored over speech being deleted, on PostgreSQL (#782 review).

``summarize_meeting`` reads a meeting's lines, lets a model take its seconds
with no transaction open, and stores the answer. A person may delete their
speech meanwhile. B's hook marks the lines forgotten and deletes the stored
summary, but while the hook's transaction is open the store step sees neither:
it would find the lines unchanged, store, and commit -- after the hook's delete
had already found no row. The summary of the deleted words would then stay.

The two take the meeting's summary lock, so the store waits for the hook, then
reads the lines without the forgotten one and stores nothing. Here the hook is
held open until the store has either queued behind the lock or, without one,
finished; the meeting must end with no summary.

The rows are committed, because both transactions have to see them, and removed
at the end (the team's meeting and everything under it cascade).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Participant, Team, Utterance
from autune_extraction import service
from autune_extraction.models import ExtForgottenUtterance, ExtMeetingSummary

KEPT = "그럼 배포는 금요일로 미루죠"
DELETED = "제가 3시까지 릴리스 노트 정리할게요"


@pytest.fixture
def meeting(db_engine: sa.Engine) -> Iterator[tuple[str, str, str]]:
    """A committed meeting of two consented lines: its id and the lines' ids."""
    tag = uuid4().hex[:12]
    kept_id, deleted_id = f"utt_kept_{tag}", f"utt_gone_{tag}"
    with Session(db_engine) as session:
        team = Team(name="팀")
        session.add(team)
        session.flush()
        row = Meeting(team_id=team.id, title="회의")
        session.add(row)
        session.flush()
        speaker = Participant(meeting_id=row.id, speaker_label="A", consented=True)
        session.add(speaker)
        session.flush()
        for n, (uid, text) in enumerate(((kept_id, KEPT), (deleted_id, DELETED))):
            session.add(
                Utterance(
                    id=uid,
                    meeting_id=row.id,
                    participant_id=speaker.id,
                    speaker_label="A",
                    start_sec=float(n),
                    end_sec=float(n) + 0.5,
                    text=text,
                )
            )
        session.commit()
        ids = (team.id, row.id, deleted_id)
    try:
        yield ids[1], kept_id, deleted_id
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == ids[0]))
            session.commit()


def someone_waits_on_an_advisory_lock(engine: sa.Engine) -> bool:
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


def test_a_summary_finished_while_speech_is_being_deleted_is_not_stored(
    db_engine: sa.Engine, meeting: tuple[str, str, str]
) -> None:
    meeting_id, _, deleted_id = meeting
    with Session(db_engine) as session:
        lines = service.summary_lines(session, meeting_id)
    assert lines == [KEPT, DELETED], "what the model was asked about"

    hook_ran = threading.Event()
    release_hook = threading.Event()
    store_done = threading.Event()
    results: dict[str, Any] = {}

    def forget() -> None:
        with Session(db_engine) as session:
            service.forget_speech(session, [deleted_id])
            hook_ran.set()
            assert release_hook.wait(timeout=10)
            session.commit()

    def store() -> None:
        with Session(db_engine) as session:
            row = service.store_meeting_summary(
                session,
                meeting_id,
                overview="릴리스 노트를 3시까지 정리하기로 했습니다.",
                points=["릴리스 노트는 3시까지 정리합니다"],
                model_version="llm:first",
                lines=lines,
            )
            results["stored"] = row is not None
            session.commit()
        store_done.set()

    hook = threading.Thread(target=forget, name="hook")
    hook.start()
    assert hook_ran.wait(timeout=10), "the hook never ran"
    task = threading.Thread(target=store, name="task")
    task.start()
    deadline = time.monotonic() + 10
    while not (someone_waits_on_an_advisory_lock(db_engine) or store_done.is_set()):
        assert time.monotonic() < deadline, "the store neither waited nor ran"
        time.sleep(0.05)
    release_hook.set()
    hook.join(timeout=10)
    task.join(timeout=10)

    assert results["stored"] is False, "the lines changed under it"
    with Session(db_engine) as session:
        assert session.get(ExtMeetingSummary, meeting_id) is None
        assert service.summary_lines(session, meeting_id) == [KEPT]


def test_the_mark_goes_with_the_utterance(
    db_engine: sa.Engine, meeting: tuple[str, str, str]
) -> None:
    """Module A deletes the utterance right after the hook; the mark has done
    its work then and must not pile up."""
    _, _, deleted_id = meeting
    with Session(db_engine) as session:
        service.forget_speech(session, [deleted_id])
        session.commit()
        assert session.get(ExtForgottenUtterance, deleted_id) is not None

        session.execute(sa.delete(Utterance).where(Utterance.id == deleted_id))
        session.commit()
        session.expire_all()

        assert session.get(ExtForgottenUtterance, deleted_id) is None
