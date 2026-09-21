"""The whole task, in order, against a real database.

Issue #9's completion criterion is that B, C and D parse a real payload, and
the order this runs in is the part that cannot be checked anywhere else: the
recording has to be gone before the flags are written, the rows have to be
committed before the event goes out, and masking has to happen before the
first ``INSERT``.

The model and the diarizer are faked. Nothing here measures their accuracy --
`docs/modules/audio-evaluations/` does that -- and a test that needed a
checkpoint would not run in CI.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_audio import tasks
from autune_audio.config import AudioSettings
from autune_audio.diarization import FakeDiarizer
from autune_audio.models import TranscriptionJob
from autune_audio.quality import TranscriptCollapsedError
from autune_audio.schemas import Segment, Transcription, Turn, Waveform, Word
from autune_contracts.events import TRANSCRIPT_READY
from autune_contracts.transcript import TranscriptReady
from autune_core.entities import Meeting, Utterance

SPOKEN = [
    ("SPEAKER_00", 0.0, 4.0, "제 번호는 010-1234-5678입니다"),
    ("SPEAKER_01", 4.2, 7.0, "네 금요일에 뵙겠습니다"),
    ("SPEAKER_00", 7.2, 9.0, "수고하셨습니다"),
]


def _transcription() -> Transcription:
    segments = tuple(
        Segment(
            start=start,
            end=end,
            text=text,
            words=(Word(start=start, end=end, text=text, probability=0.9),),
        )
        for _, start, end, text in SPOKEN
    )
    return Transcription(segments=segments, language="ko", language_probability=0.99, duration=9.0)


def _turns() -> tuple[Turn, ...]:
    return tuple(Turn(start=start, end=end, speaker=speaker) for speaker, start, end, _ in SPOKEN)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AudioSettings:
    """The worker's scratch directory, owned by this test."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = AudioSettings(temp_dir=str(scratch))
    monkeypatch.setattr(tasks, "get_settings", lambda: fake)
    return fake


@pytest.fixture
def job(db_session: Session, meeting: str) -> str:
    """A queued attempt on a claimed meeting -- what the upload route leaves.

    The route sets the meeting ``analyzing`` and creates the job in one
    transaction; a task only ever runs on a meeting in that state.
    """
    db_session.get(Meeting, meeting).status = "analyzing"
    row = TranscriptionJob(meeting_id=meeting, status="queued")
    db_session.add(row)
    db_session.flush()
    return row.id


@pytest.fixture
def recording(job: str, settings: AudioSettings) -> Path:
    """The file where the worker will look for it: ``{job_id}.upload``."""
    path = Path(settings.temp_dir) / f"{job}.upload"
    path.write_bytes(b"not really audio; decode is faked")
    return path


@pytest.fixture
def published() -> list[tuple[str, dict]]:
    return []


@pytest.fixture
def pipeline(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    published: list[tuple[str, dict]],
    recording: Path,
) -> Iterator[dict]:
    """Everything the task reaches outside its own logic, faked in one place."""
    state: dict = {
        "transcription": _transcription(),
        "audio_present_at_write": None,
        "order": [],
    }

    monkeypatch.setattr(
        tasks, "decode", lambda path: Waveform(samples=np.zeros(160, dtype=np.float32))
    )
    monkeypatch.setattr(tasks, "transcribe", lambda waveform, **kw: state["transcription"])
    monkeypatch.setattr(tasks, "get_diarizer", lambda: FakeDiarizer(_turns()))
    monkeypatch.setattr(tasks, "build_prompt", lambda: "")

    def fake_publish(event: str, payload: dict) -> list[str]:
        published.append((event, payload))
        state["order"].append("publish")
        return []

    monkeypatch.setattr(tasks, "publish", fake_publish)

    class Scope:
        """The task's own session, bound to the test's rolled-back transaction.

        ``__exit__`` records where the real ``session_scope`` would commit, so
        ``state["order"]`` can say whether the event went out before or after
        the transaction closed. Flushed rather than committed, because the test
        rolls the whole thing back.
        """

        def __enter__(self) -> Session:
            state["audio_present_at_write"] = recording.exists()
            return db_session

        def __exit__(self, *exc: object) -> None:
            db_session.flush()
            state["order"].append("commit")

    monkeypatch.setattr(tasks, "session_scope", Scope)
    yield state


def test_the_event_carries_what_the_database_holds(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    published: list[tuple[str, dict]],
) -> None:
    """#9's completion criterion, end to end.

    The payload is validated the way a consumer validates it -- from the dict
    that went on the wire, then through `require_privacy_guarantees`, which is
    the first thing B, C and D each call.
    """
    tasks.process_recording(job)

    assert [event for event, _ in published] == [TRANSCRIPT_READY]
    payload = TranscriptReady.model_validate(published[0][1])
    payload.require_privacy_guarantees()

    rows = list(
        db_session.scalars(
            sa.select(Utterance)
            .where(Utterance.meeting_id == meeting)
            .order_by(Utterance.start_sec)
        )
    )
    assert [u.id for u in payload.utterances] == [row.id for row in rows]
    assert [u.text for u in payload.utterances] == [row.text for row in rows]
    assert payload.metadata.participants == ["SPEAKER_00", "SPEAKER_01"]
    assert payload.metadata.duration == 9.0


def test_the_event_goes_out_after_the_transaction_closes(
    pipeline: dict, job: str, meeting: str, recording: Path
) -> None:
    """Four modules act on this event; a rollback after it has gone is four
    modules processing a meeting that does not exist.

    Ordering alone is not enough to catch this -- the publish call sits after
    the `with` block textually either way. What makes it checkable is the
    session scope recording where it closed, so moving `publish` inside it
    changes the order this asserts. Without that, moving the call left all
    nineteen tests passing (@kjfcvx12 on #184), and the PR description claimed
    the opposite.
    """
    tasks.process_recording(job)
    # The claim's transaction, the write's transaction, the event, then the
    # one-line transaction that marks the job done.
    assert pipeline["order"] == ["commit", "commit", "publish", "commit"]


def test_the_recording_is_gone_before_anything_is_written(
    pipeline: dict, job: str, meeting: str, recording: Path, published: list[tuple[str, dict]]
) -> None:
    """`original_audio_deleted` is read from the filesystem, so the order is the
    proof. Written inside the `adopt` block, the flag would be False -- and a
    False flag is one every consumer refuses on."""
    tasks.process_recording(job)

    assert pipeline["audio_present_at_write"] is False
    assert not recording.exists()
    assert TranscriptReady.model_validate(published[0][1]).metadata.privacy.original_audio_deleted


def test_personal_data_is_masked_before_the_first_insert(
    pipeline: dict, db_session: Session, job: str, meeting: str, recording: Path
) -> None:
    """privacy.md section 2's line, checked at the place it is drawn.

    `persist_transcript` also refuses unmasked text, so the row existing at all
    is half the assertion; the other half is that the number is not in it.
    """
    tasks.process_recording(job)

    texts = list(
        db_session.scalars(sa.select(Utterance.text).where(Utterance.meeting_id == meeting))
    )
    assert "010-1234-5678" not in " ".join(texts)
    assert "010-****-5678" in " ".join(texts)


def test_a_collapsed_transcript_is_not_written_and_not_published(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    published: list[tuple[str, dict]],
) -> None:
    """Refused after the recording is deleted, which is the only order there is.

    The fallback pass already ran inside `transcribe`; by here there is nothing
    left to try and nothing left to read, so this fails the job rather than
    writing a meeting of one repeated sentence.
    """
    line = "그래서 그 부분은 다시 확인해보겠습니다"
    pipeline["transcription"] = Transcription(
        segments=tuple(
            Segment(
                start=float(i),
                end=float(i) + 1,
                text=line,
                words=(Word(start=float(i), end=float(i) + 1, text=line, probability=0.9),),
            )
            for i in range(40)
        ),
        language="ko",
        language_probability=0.99,
        duration=40.0,
    )

    with pytest.raises(TranscriptCollapsedError):
        tasks.process_recording(job)

    assert published == []
    assert not recording.exists()
    row = db_session.get(Meeting, meeting)
    assert row is not None
    assert row.pii_masked is False


def test_a_finished_meeting_is_marked_complete(
    pipeline: dict, db_session: Session, job: str, meeting: str, recording: Path
) -> None:
    """The status is what a screen reads to tell "not yet" from "nothing said".

    ``transcript_for_meeting`` returns an empty list all the way through the
    task — every utterance is written in one transaction at the end — so a
    meeting stuck at ``analyzing`` and a meeting where nobody spoke look
    identical to a reader that does not have this.
    """
    tasks.process_recording(job)

    assert db_session.get(Meeting, meeting).status == "complete"


def test_a_meeting_whose_task_raised_is_marked_failed(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    monkeypatch: pytest.MonkeyPatch,
    published: list[tuple[str, dict]],
) -> None:
    """The recording is gone and it is not coming back — say so.

    ``adopt`` deletes in a ``finally``, so a failed run has already destroyed
    the only copy of the audio. Leaving the meeting at ``analyzing`` would make
    the screen wait for a task that is never going to report, and would hide
    from the uploader that the one thing they could have retried is gone.
    """

    def explode(*_: object, **__: object) -> None:
        raise RuntimeError("pyannote could not load")

    monkeypatch.setattr(tasks, "assign_speakers", explode)

    with pytest.raises(RuntimeError, match="pyannote could not load"):
        tasks.process_recording(job)

    assert db_session.get(Meeting, meeting).status == "failed"
    assert db_session.get(TranscriptionJob, job).status == "failed"
    assert published == []


def test_a_finished_job_records_it(
    pipeline: dict, db_session: Session, job: str, meeting: str, recording: Path
) -> None:
    tasks.process_recording(job)

    row = db_session.get(TranscriptionJob, job)
    assert row.status == "done"
    assert row.finished_at is not None


def test_a_redelivery_after_completion_is_declined_and_does_not_undo_complete(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    published: list[tuple[str, dict]],
) -> None:
    """``acks_late``'s other edge: the worker dies *after* the commit and the
    publish, *before* the ack. The broker redelivers.

    The redelivered run finds its job ``done`` and stops at the door. Its
    transcript is in the database and four modules have already been told;
    running again would republish, and failing would make S12 draw red over
    a meeting that finished (@PARKJAEKYUNG0525 on #259). Whatever is at the
    job's path -- nothing, normally -- is deleted, because this attempt owns
    it and nobody else will.
    """
    tasks.process_recording(job)
    assert db_session.get(Meeting, meeting).status == "complete"

    recording.write_bytes(b"redelivered")
    tasks.process_recording(job)

    assert db_session.get(Meeting, meeting).status == "complete"
    assert db_session.get(TranscriptionJob, job).status == "done"
    assert not recording.exists()
    assert len(published) == 1


def test_a_superseded_attempt_deletes_its_file_and_touches_nothing(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    published: list[tuple[str, dict]],
) -> None:
    """The late first attempt from #275, arriving after a second was accepted.

    The meeting belongs to the second attempt now. This one must not
    transcribe (it would race), must not fail the meeting (nothing about the
    current attempt failed), and must delete the file it was queued for
    (nobody else will).
    """
    db_session.get(TranscriptionJob, job).status = "superseded"
    db_session.flush()

    tasks.process_recording(job)

    assert recording.exists() is False
    assert db_session.get(Meeting, meeting).status == "analyzing"
    assert db_session.get(TranscriptionJob, job).status == "superseded"
    assert published == []


def test_a_job_nobody_queued_is_loud(pipeline: dict, settings: AudioSettings) -> None:
    from autune_core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        tasks.process_recording("job_nobody_queued_this")


# --- the sweep ----------------------------------------------------------------


def _upload(settings: AudioSettings, job_id: str) -> Path:
    path = Path(settings.temp_dir) / f"{job_id}.upload"
    path.write_bytes(b"left behind")
    return path


def _job(db_session: Session, meeting: str, status: str, *, age: timedelta = timedelta()) -> str:
    row = TranscriptionJob(meeting_id=meeting, status=status, created_at=datetime.now(tz=UTC) - age)
    db_session.add(row)
    db_session.flush()
    return row.id


def test_the_sweep_collects_uploads_whose_attempt_is_over(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    settings: AudioSettings,
) -> None:
    """A file for a ``done``, ``failed`` or ``superseded`` job is a recording
    nobody owns -- the case #259's review said had no collector. Decided
    against the database, not the clock, so a file a late task is about to
    adopt is never in this set."""
    leftovers = {
        status: _upload(settings, _job(db_session, meeting, status))
        for status in ("done", "failed", "superseded")
    }
    unknown = _upload(settings, "job_nobody_knows")

    tasks.process_recording(job)

    assert all(not path.exists() for path in leftovers.values())
    assert not unknown.exists()


def test_the_sweep_leaves_a_live_attempt_alone(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    settings: AudioSettings,
) -> None:
    """#209's sweep could delete a file a late task was about to adopt. A
    ``queued`` job younger than the threshold is exactly that file."""
    other = _job(db_session, meeting, "queued")
    waiting = _upload(settings, other)

    tasks.process_recording(job)

    assert waiting.exists()
    assert db_session.get(TranscriptionJob, other).status == "queued"


def test_the_sweep_fails_an_attempt_that_has_been_running_too_long(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    settings: AudioSettings,
) -> None:
    """A job older than ``orphan_after_hours`` has no worker. Its file is
    deleted and the job failed; the meeting is not, because it is now the
    current attempt's (the one running this test)."""
    stale = _job(
        db_session, meeting, "running", age=timedelta(hours=settings.orphan_after_hours + 1)
    )
    abandoned = _upload(settings, stale)

    tasks.process_recording(job)

    assert not abandoned.exists()
    assert db_session.get(TranscriptionJob, stale).status == "failed"
    assert db_session.get(Meeting, meeting).status == "complete"


def test_the_sweep_collects_a_write_that_never_reached_a_claim(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    settings: AudioSettings,
) -> None:
    """``handover`` writes under a throwaway name and renames at the claim. A
    request that died in between leaves a file with no job to consult; the
    write-to-claim window is one request, so age is the only question."""
    import os

    fresh = Path(settings.temp_dir) / "tmpfresh"
    fresh.write_bytes(b"still being uploaded")
    old = Path(settings.temp_dir) / "tmpold"
    old.write_bytes(b"request died here")
    ago = (datetime.now(tz=UTC) - timedelta(hours=settings.orphan_after_hours + 1)).timestamp()
    os.utime(old, (ago, ago))

    tasks.process_recording(job)

    assert fresh.exists()
    assert not old.exists()


def test_a_failed_publish_fails_the_meeting_so_it_can_be_re_uploaded(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dead end @kjfcvx12 found on #259: transcript committed, meeting
    ``complete``, then the broker refuses the event -- four modules never
    hear of the meeting and a ``complete`` meeting refuses another upload.

    The rows stay (they are masked and correct); the meeting goes to
    ``failed`` and accepts a re-upload. That re-run replaces the utterances,
    which is #194's id churn -- accepted here as the lesser cost.
    """

    def broker_down(*_: object, **__: object) -> list[str]:
        raise ConnectionError("broker refused the event")

    monkeypatch.setattr(tasks, "publish", broker_down)

    with pytest.raises(ConnectionError):
        tasks.process_recording(job)

    assert db_session.get(Meeting, meeting).status == "failed"
    assert db_session.get(TranscriptionJob, job).status == "failed"
    stored = db_session.scalars(
        sa.select(sa.func.count()).select_from(Utterance).where(Utterance.meeting_id == meeting)
    ).one()
    assert stored == len(SPOKEN), "the transcript is kept; only the announcement failed"
