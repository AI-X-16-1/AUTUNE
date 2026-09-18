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
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_audio import tasks
from autune_audio.diarization import FakeDiarizer
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
def recording(tmp_path: Path) -> Path:
    path = tmp_path / "meeting.m4a"
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
    meeting: str,
    recording: Path,
    published: list[tuple[str, dict]],
) -> None:
    """#9's completion criterion, end to end.

    The payload is validated the way a consumer validates it -- from the dict
    that went on the wire, then through `require_privacy_guarantees`, which is
    the first thing B, C and D each call.
    """
    tasks.process_recording(meeting, str(recording))

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
    pipeline: dict, meeting: str, recording: Path
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
    tasks.process_recording(meeting, str(recording))
    assert pipeline["order"] == ["commit", "publish"]


def test_the_recording_is_gone_before_anything_is_written(
    pipeline: dict, meeting: str, recording: Path, published: list[tuple[str, dict]]
) -> None:
    """`original_audio_deleted` is read from the filesystem, so the order is the
    proof. Written inside the `adopt` block, the flag would be False -- and a
    False flag is one every consumer refuses on."""
    tasks.process_recording(meeting, str(recording))

    assert pipeline["audio_present_at_write"] is False
    assert not recording.exists()
    assert TranscriptReady.model_validate(published[0][1]).metadata.privacy.original_audio_deleted


def test_personal_data_is_masked_before_the_first_insert(
    pipeline: dict, db_session: Session, meeting: str, recording: Path
) -> None:
    """privacy.md section 2's line, checked at the place it is drawn.

    `persist_transcript` also refuses unmasked text, so the row existing at all
    is half the assertion; the other half is that the number is not in it.
    """
    tasks.process_recording(meeting, str(recording))

    texts = list(
        db_session.scalars(sa.select(Utterance.text).where(Utterance.meeting_id == meeting))
    )
    assert "010-1234-5678" not in " ".join(texts)
    assert "010-****-5678" in " ".join(texts)


def test_running_it_twice_leaves_one_meeting_and_republishes_it(
    pipeline: dict,
    db_session: Session,
    meeting: str,
    recording: Path,
    published: list[tuple[str, dict]],
) -> None:
    """What `acks_late` makes a requirement rather than a nicety.

    The second run gets a deleted recording, so `adopt` is given a path that no
    longer exists -- the shape a redelivery actually takes. Utterance ids are
    generated at the write, so they differ; everything a consumer keys on does
    not.
    """
    tasks.process_recording(meeting, str(recording))
    recording.write_bytes(b"redelivered")
    tasks.process_recording(meeting, str(recording))

    rows = db_session.scalars(
        sa.select(sa.func.count()).select_from(Utterance).where(Utterance.meeting_id == meeting)
    ).one()
    assert rows == len(SPOKEN)

    first, second = (TranscriptReady.model_validate(p) for _, p in published)
    assert [u.text for u in first.utterances] == [u.text for u in second.utterances]
    assert first.metadata == second.metadata


def test_a_collapsed_transcript_is_not_written_and_not_published(
    pipeline: dict,
    db_session: Session,
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
        tasks.process_recording(meeting, str(recording))

    assert published == []
    assert not recording.exists()
    row = db_session.get(Meeting, meeting)
    assert row is not None
    assert row.pii_masked is False


def test_a_finished_meeting_is_marked_complete(
    pipeline: dict, db_session: Session, meeting: str, recording: Path
) -> None:
    """The status is what a screen reads to tell "not yet" from "nothing said".

    ``transcript_for_meeting`` returns an empty list all the way through the
    task — every utterance is written in one transaction at the end — so a
    meeting stuck at ``analyzing`` and a meeting where nobody spoke look
    identical to a reader that does not have this.
    """
    tasks.process_recording(meeting, str(recording))

    assert db_session.get(Meeting, meeting).status == "complete"


def test_a_meeting_whose_task_raised_is_marked_failed(
    pipeline: dict,
    db_session: Session,
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
        tasks.process_recording(meeting, str(recording))

    assert db_session.get(Meeting, meeting).status == "failed"
    assert published == []
