"""The worker's heartbeat, and the way a cancel reaches a running task.

A thread writes ``aud_jobs.heartbeat_at`` every ``interval_s`` and reads the
job's status back in the same statement. Two questions, one round trip:

- **Is the worker alive?** The API reads the heartbeat's age
  (``service.is_stalled``). Progress cannot answer this: diarization and an
  external STT provider can go minutes without reporting any.
- **Should it stop?** A status other than ``running`` -- ``cancelled`` by a
  person, ``superseded`` by a restart or a new upload -- sets a flag, and
  ``check()`` turns the flag into ``JobStopped`` wherever the task asks.

Cancel by flag rather than ``revoke``: terminate does nothing on the solo pool
the Mac worker runs (#329) and would kill a task mid-write anywhere else.

Nothing written here is meeting content: a timestamp and a status.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from types import TracebackType

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import get_logger, session_scope

from .models import TranscriptionJob

log = get_logger(__name__)

Beat = Callable[[], str | None]


class JobStopped(Exception):  # noqa: N818 - a stop, not an error
    """The job is no longer ``running``: cancelled, superseded, or gone."""

    def __init__(self, status: str | None) -> None:
        super().__init__(f"job is {status}")
        self.status = status


def beat_with(session: Session, job_id: str) -> str | None:
    """Write the heartbeat and return the job's status; ``None`` if no row."""
    return session.scalar(
        sa.update(TranscriptionJob)
        .where(TranscriptionJob.id == job_id)
        .values(heartbeat_at=sa.func.now())
        .returning(TranscriptionJob.status)
    )


def _beat_in_own_session(job_id: str) -> Beat:
    def beat() -> str | None:
        with session_scope() as session:
            return beat_with(session, job_id)

    return beat


class JobGuard:
    def __init__(self, job_id: str, *, interval_s: float, beat: Beat | None = None) -> None:
        self._job_id = job_id
        self._interval_s = interval_s
        self._beat = beat or _beat_in_own_session(job_id)
        self._stop = threading.Event()
        self._closing = threading.Event()
        self._stopped_as: str | None = None
        self._thread: threading.Thread | None = None

    def poll(self) -> None:
        """One heartbeat. A beat that raises is logged and ignored: a
        database blip is not a cancel, and the next beat decides."""
        try:
            status = self._beat()
        except Exception as error:  # noqa: BLE001 - must never fail the job
            log.warning("audio_heartbeat_failed", job_id=self._job_id, error=type(error).__name__)
            return
        if status != "running":
            self._stopped_as = status
            self._stop.set()

    def check(self) -> None:
        if self._stop.is_set():
            raise JobStopped(self._stopped_as)

    def _run(self) -> None:
        while not self._closing.wait(self._interval_s):
            self.poll()

    def __enter__(self) -> JobGuard:
        self.poll()
        self._thread = threading.Thread(
            target=self._run, name=f"job-guard-{self._job_id}", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._closing.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s + 5)
