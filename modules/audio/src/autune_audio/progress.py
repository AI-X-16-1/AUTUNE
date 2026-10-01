"""How far ``process_recording`` has got, written where S12 can read it.

The task used to write one thing while it ran -- the meeting's status -- so the
screen could only say "analyzing" for the whole of a transcription that takes
about as long as the meeting did. This records the step and a fraction of it on
the job row (``aud_jobs.stage``, ``aud_jobs.stage_progress``), and
``GET /meetings/{id}`` returns them.

Two rules shape it, because it is called from inside Whisper's and pyannote's
loops:

- **Cheap to call often.** A stage change is written at once; within a stage a
  write happens at most once per ``min_interval`` seconds and only when the
  fraction moved by at least ``min_step``. Thousands of calls cost a few dozen
  ``UPDATE``s.
- **Never fails the job.** Progress is a courtesy to the person watching. A
  write that raises is logged and dropped; the transcription carries on.

Nothing written here is meeting content: a step name and a number.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import sqlalchemy as sa

from autune_core import get_logger, session_scope

from .models import TranscriptionJob

log = get_logger(__name__)

STAGES = ("decoding", "transcribing", "diarizing", "masking", "saving")
"""In the order ``process_recording`` runs them. The recording is deleted when
``diarizing`` ends -- ``storage.adopt`` closes there -- so by ``masking`` the
original is already gone."""

Write = Callable[[str, float | None], None]


def _write_to_job(job_id: str) -> Write:
    def write(stage: str, progress: float | None) -> None:
        with session_scope() as session:
            session.execute(
                sa.update(TranscriptionJob)
                .where(TranscriptionJob.id == job_id)
                .values(stage=stage, stage_progress=progress)
            )

    return write


class ProgressReporter:
    def __init__(
        self,
        job_id: str,
        *,
        write: Write | None = None,
        clock: Callable[[], float] = time.monotonic,
        min_interval: float = 1.0,
        min_step: float = 0.01,
    ) -> None:
        self._job_id = job_id
        self._write = write or _write_to_job(job_id)
        self._clock = clock
        self._min_interval = min_interval
        self._min_step = min_step
        self._stage: str | None = None
        self._last_at = 0.0
        self._last_value = 0.0

    def stage(self, name: str) -> None:
        """Enter a step. Written immediately, at 0."""
        self._stage = name
        self._emit(0.0)

    def update(self, fraction: float) -> None:
        """How far through the current step, 0..1. Throttled."""
        if self._stage is None:
            return
        value = min(1.0, max(0.0, fraction))
        if self._clock() - self._last_at < self._min_interval:
            return
        if abs(value - self._last_value) < self._min_step:
            return
        self._emit(value)

    def _emit(self, value: float) -> None:
        assert self._stage is not None
        self._last_at = self._clock()
        self._last_value = value
        try:
            self._write(self._stage, value)
        except Exception as error:  # noqa: BLE001 - progress must never fail the job
            log.warning(
                "audio_progress_write_failed",
                job_id=self._job_id,
                stage=self._stage,
                error=type(error).__name__,
            )
