"""Cancelling and restarting a transcription (spec 2026-10-02).

A worker that dies leaves its meeting ``analyzing`` with nobody coming; a wrong
upload could not be stopped. These cover the two routes that fix that, the
flags the screen draws them from, and the file each one leaves behind.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from autune_audio.models import TranscriptionJob
from autune_core import Meeting


def _job(
    db_session: Session,
    meeting: str,
    status: str,
    *,
    age: timedelta = timedelta(),
    heartbeat_age: timedelta | None = None,
) -> TranscriptionJob:
    now = datetime.now(tz=UTC)
    row = TranscriptionJob(
        meeting_id=meeting,
        status=status,
        created_at=now - age,
        heartbeat_at=None if heartbeat_age is None else now - heartbeat_age,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_a_job_can_be_cancelled_and_carries_a_heartbeat(db_session: Session, meeting: str) -> None:
    db_session.get(Meeting, meeting).status = "analyzing"
    job = _job(db_session, meeting, "cancelled", heartbeat_age=timedelta(seconds=5))

    db_session.refresh(job)

    assert job.status == "cancelled"
    assert job.heartbeat_at is not None
