# Transcription cancel and restart — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person can cancel a transcription in progress, and restart one whose worker died, from the S12 screen, without raw audio living longer than it does today.

**Architecture:** The worker runs a `JobGuard` thread that writes `aud_jobs.heartbeat_at` and reads the job's status back in one statement. A status other than `running` sets a stop flag that `ProgressReporter` and a lock-based fence before the transcript write turn into `JobStopped`. Two module-A routes flip the job (`cancelled`, or `superseded` plus a new job that takes over the same upload file), and `GET /meetings/{id}` gains four flags the screen draws buttons from.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic (audio branch), Celery, pytest against Postgres; Next.js, React, vitest + Testing Library.

**Spec:** `modules/audio/docs/specs/2026-10-02-transcription-cancel-restart-design.md`

## Global Constraints

- Module A only: write `aud_*` tables and the `meetings` row (A is its writer); no import of another module; no change to `packages/contracts`. `meetings.status` gains no new value.
- Invariant 11: raw audio is never kept longer than today. The `orphan_after_hours` (6 h) clock runs from the upload, not from the latest attempt.
- One owner per upload file at a time: worker (`adopt`), or the API only for a stalled job.
- Lock order is always **meeting row, then job row** (`start_transcription` already does this). Anything that locks both follows it.
- No paths, transcript text or audio in a log line or a Celery payload; ids and statuses only.
- New settings: `heartbeat_interval_s = 30`, `stall_after_s = 120` (env `AUTUNE_AUDIO_HEARTBEAT_INTERVAL_S`, `AUTUNE_AUDIO_STALL_AFTER_S`), provisional, recorded in `modules/audio/HISTORY.md`.
- Error codes: `nothing_to_cancel` (409), `not_stalled` (409), `recording_gone` (409).
- Repo text in English; screen copy in Korean, passed through a Korean-writing pass so it does not read translated.
- Backend tests run from the worktree with a throwaway database (see "Running tests" below), never the shared one.

## Review Focus

1. **Cancel and the transcript write land together.** The worker is inside the saving transaction when the cancel arrives: one of them must win cleanly, never a deadlock and never a `cancelled` job with a stored transcript. Pinned in Task 4 (fence test) and by the lock order.
2. **A worker that dies between `mark_complete` and `mark_published`** leaves job `running`, meeting `complete`. Restart must refuse it (meeting not `analyzing`) or it would re-transcribe a delivered meeting. Pinned in Task 7.
3. **Restarting again and again does not extend the six hours.** Pinned in Task 5 and Task 7 (`recording_gone` past the window).
4. **A database blip in the heartbeat thread does not stop or crash the job.** Pinned in Task 2.
5. **Double click on 다시 시작 / 처리 취소.** The second request must get a 409, not a second job. Pinned in Task 6 and Task 7.

## Running tests

From the worktree root, once:

```bash
uv sync --all-packages
createdb -h localhost -U autune autune_cancel_restart 2>/dev/null || true
export AUTUNE_DATABASE_URL="postgresql+psycopg://autune:autune@localhost:5432/autune_cancel_restart"
```

Backend: `uv run pytest modules/audio/tests -q` (integration tests run `alembic upgrade heads` themselves).
Web: `cd apps/web && pnpm install && pnpm test && pnpm run typecheck && pnpm run lint && pnpm run build`.

## File map

| File | Responsibility |
| --- | --- |
| `modules/audio/migrations/20261002_1200_aud_jobs_cancel_and_heartbeat.py` (new) | `cancelled` status, `heartbeat_at` column |
| `modules/audio/src/autune_audio/models.py` | `TranscriptionJob` mirrors the migration |
| `modules/audio/src/autune_audio/config.py` | two settings |
| `modules/audio/src/autune_audio/job_guard.py` (new) | `JobGuard`, `JobStopped`, `beat` |
| `modules/audio/src/autune_audio/progress.py` | `ProgressReporter(check=...)` |
| `modules/audio/src/autune_audio/tasks.py` | guard around the task, fence, `JobStopped` handling |
| `modules/audio/src/autune_audio/service.py` | `lock_running_job`, sweep clock, cancel, restart, controls |
| `modules/audio/src/autune_audio/router.py` | two routes, `MeetingDetail` flags |
| `modules/audio/src/autune_audio/schemas.py` | `MeetingDetail` fields |
| `modules/audio/tests/unit/test_job_guard.py` (new), `tests/unit/test_progress.py` | guard and reporter |
| `modules/audio/tests/integration/test_tasks.py` | worker behaviour |
| `modules/audio/tests/integration/test_cancel_restart.py` (new) | service, routes, flags |
| `apps/web/src/features/transcript/{types.ts,api.ts}` | flags, two calls |
| `apps/web/src/features/transcript/components/TranscriptionControls.tsx` (new) + `.test.tsx` | the buttons and notices |
| `apps/web/src/features/transcript/components/ProcessingStages.tsx` | mounts the controls; 취소됨 wording |
| `.env.example`, `docs/engineering/environments.md`, `modules/audio/HISTORY.md`, spec | docs |

---

### Task 1: Data model and settings

**Files:**
- Create: `modules/audio/migrations/20261002_1200_aud_jobs_cancel_and_heartbeat.py`
- Modify: `modules/audio/src/autune_audio/models.py` (the `TranscriptionJob` class, `__table_args__` and columns)
- Modify: `modules/audio/src/autune_audio/config.py` (after `orphan_after_hours`)
- Modify: `.env.example` (after `AUTUNE_AUDIO_ORPHAN_AFTER_HOURS=6`), `docs/engineering/environments.md` (after the `AUTUNE_AUDIO_ORPHAN_AFTER_HOURS` row)
- Test: `modules/audio/tests/integration/test_cancel_restart.py` (new)

**Interfaces:**
- Produces: `TranscriptionJob.heartbeat_at: datetime | None`; job status value `"cancelled"`; `AudioSettings.heartbeat_interval_s: float`, `AudioSettings.stall_after_s: float`.

- [ ] **Step 1: Write the failing test**

Create `modules/audio/tests/integration/test_cancel_restart.py`:

```text
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest modules/audio/tests/integration/test_cancel_restart.py -q`
Expected: FAIL — `TypeError: 'heartbeat_at' is an invalid keyword argument for TranscriptionJob`.

- [ ] **Step 3: Write the migration, model and settings**

`modules/audio/migrations/20261002_1200_aud_jobs_cancel_and_heartbeat.py`:

```text
"""cancelled status and heartbeat_at on aud_jobs

A worker that dies mid-job left its meeting analyzing forever, and a wrong
upload could not be stopped. ``heartbeat_at`` is how the API tells a dead
worker from a slow one; ``cancelled`` is what a person's cancel writes. Nothing
here is meeting content: a status and a timestamp.

Owner: 김민경.

Revision ID: 5c1e9a7d3b20
Revises: d2e8b04f6a17
Create Date: 2026-10-02 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c1e9a7d3b20"
down_revision: str | None = "d2e8b04f6a17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = "status IN ('queued','running','done','failed','superseded')"
_AFTER = "status IN ('queued','running','done','failed','superseded','cancelled')"


def upgrade() -> None:
    op.add_column("aud_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("ck_aud_jobs_status", "aud_jobs", type_="check")
    op.create_check_constraint("ck_aud_jobs_status", "aud_jobs", _AFTER)


def downgrade() -> None:
    # A cancelled attempt is over; failed is the nearest status the old
    # constraint knows.
    op.execute("UPDATE aud_jobs SET status = 'failed' WHERE status = 'cancelled'")
    op.drop_constraint("ck_aud_jobs_status", "aud_jobs", type_="check")
    op.create_check_constraint("ck_aud_jobs_status", "aud_jobs", _BEFORE)
    op.drop_column("aud_jobs", "heartbeat_at")
```

Before writing it, confirm the head: `grep -rln "d2e8b04f6a17" modules/audio/migrations/` must list only `20261001_1700_add_aud_masking_rules.py`. If another audio revision has landed on main since, chain onto that one instead.

In `models.py`, `TranscriptionJob.__table_args__`, change the status constraint to:

```text
        CheckConstraint(
            "status IN ('queued','running','done','failed','superseded','cancelled')",
            name="ck_aud_jobs_status",
        ),
```

and add after `stage_progress`:

```text
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Written by the worker's ``JobGuard`` when it claims the job and every
    ``heartbeat_interval_s`` after. A ``running`` job whose heartbeat is older
    than ``stall_after_s`` has no worker (``service.is_stalled``)."""
```

Also add `cancelled` to the class docstring's list of statuses that set `finished_at` ("``finished_at`` is set on the last three" becomes "the last four").

In `config.py`, after `orphan_after_hours`:

```text
    heartbeat_interval_s: float = 30.0
    """How often a running job's ``JobGuard`` writes ``aud_jobs.heartbeat_at``
    and reads back whether it has been cancelled or superseded. Also the
    longest a cancel waits before the worker sees it. Provisional (HISTORY.md).
    """

    stall_after_s: float = 120.0
    """How old a running job's heartbeat may be before the job counts as
    stalled -- its worker gone -- and may be restarted. Four missed heartbeats.
    Provisional (HISTORY.md)."""
```

In `.env.example` after `AUTUNE_AUDIO_ORPHAN_AFTER_HOURS=6`:

```
AUTUNE_AUDIO_HEARTBEAT_INTERVAL_S=30
AUTUNE_AUDIO_STALL_AFTER_S=120
```

In `docs/engineering/environments.md`, after the `AUTUNE_AUDIO_ORPHAN_AFTER_HOURS` row:

```
| `AUTUNE_AUDIO_HEARTBEAT_INTERVAL_S` | A | How often a running transcription writes its heartbeat and checks whether it was cancelled. Default `30` |
| `AUTUNE_AUDIO_STALL_AFTER_S` | A | A running transcription whose heartbeat is older than this has no worker and may be restarted. Default `120` |
```

- [ ] **Step 4: Run test to verify it passes, and the migration round-trips**

Run: `uv run pytest modules/audio/tests/integration/test_cancel_restart.py -q`
Expected: PASS.

Run: `uv run alembic -c infra/alembic.ini downgrade audio@-1 && uv run alembic -c infra/alembic.ini upgrade heads`
Expected: both succeed with no error.

- [ ] **Step 5: Commit**

```bash
git add modules/audio/migrations/20261002_1200_aud_jobs_cancel_and_heartbeat.py modules/audio/src/autune_audio/models.py modules/audio/src/autune_audio/config.py .env.example docs/engineering/environments.md modules/audio/tests/integration/test_cancel_restart.py
git commit -m "feat(audio): aud_jobs can be cancelled and carries a heartbeat"
```

---

### Task 2: `JobGuard`

**Files:**
- Create: `modules/audio/src/autune_audio/job_guard.py`
- Test: `modules/audio/tests/unit/test_job_guard.py`

**Interfaces:**
- Consumes: `TranscriptionJob.heartbeat_at`, status `"cancelled"` (Task 1).
- Produces:
  - `class JobStopped(Exception)` with attribute `status: str | None`.
  - `Beat = Callable[[], str | None]`.
  - `def beat_with(session: Session, job_id: str) -> str | None` — the heartbeat statement on a given session.
  - `class JobGuard(job_id: str, *, interval_s: float, beat: Beat | None = None)` — context manager; `poll() -> None`; `check() -> None` (raises `JobStopped`).

- [ ] **Step 1: Write the failing tests**

`modules/audio/tests/unit/test_job_guard.py`:

```text
"""The worker's heartbeat, and how it hears a cancel.

The beat is injected: these tests are about what the guard does with the
answer, not about the database (``test_tasks.py`` covers the real statement).
"""

from __future__ import annotations

import threading

import pytest

from autune_audio.job_guard import JobGuard, JobStopped


class Beats:
    """A beat that answers from a list and counts calls."""

    def __init__(self, *answers: str | None) -> None:
        self.answers = list(answers)
        self.calls = 0
        self.called = threading.Event()

    def __call__(self) -> str | None:
        self.calls += 1
        self.called.set()
        if not self.answers:
            return "running"
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_entering_beats_at_once() -> None:
    beats = Beats("running")
    with JobGuard("job_1", interval_s=3600, beat=beats):
        assert beats.calls == 1


def test_a_running_job_passes_the_check() -> None:
    with JobGuard("job_1", interval_s=3600, beat=Beats("running")) as guard:
        guard.check()


@pytest.mark.parametrize("status", ["cancelled", "superseded", None])
def test_any_other_status_stops_the_job(status: str | None) -> None:
    beats = Beats("running", status)
    with JobGuard("job_1", interval_s=3600, beat=beats) as guard:
        guard.check()
        guard.poll()
        with pytest.raises(JobStopped) as stopped:
            guard.check()
    assert stopped.value.status == status


def test_a_beat_that_raises_does_not_stop_the_job() -> None:
    """A database blip is not a cancel. The next beat decides."""
    beats = Beats("running", RuntimeError("connection reset"))
    with JobGuard("job_1", interval_s=3600, beat=beats) as guard:
        guard.poll()
        guard.check()


def test_the_thread_beats_on_its_interval_and_stops_on_exit() -> None:
    beats = Beats("running")
    with JobGuard("job_1", interval_s=0.01, beat=beats) as guard:
        beats.called.clear()
        assert beats.called.wait(timeout=2)
        thread = guard._thread
    assert thread is not None
    assert not thread.is_alive()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest modules/audio/tests/unit/test_job_guard.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autune_audio.job_guard'`.

- [ ] **Step 3: Write `job_guard.py`**

```text
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest modules/audio/tests/unit/test_job_guard.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add modules/audio/src/autune_audio/job_guard.py modules/audio/tests/unit/test_job_guard.py
git commit -m "feat(audio): a job guard writes the heartbeat and hears a cancel"
```

---

### Task 3: `ProgressReporter` checks the guard

**Files:**
- Modify: `modules/audio/src/autune_audio/progress.py` (`ProgressReporter.__init__`, `stage`, `update`)
- Test: `modules/audio/tests/unit/test_progress.py`

**Interfaces:**
- Consumes: `JobGuard.check` (Task 2) — passed in as a plain callable.
- Produces: `ProgressReporter(job_id, *, check: Callable[[], None] | None = None, ...)`. `stage()` and `update()` call `check()` first, on every call, before throttling, and outside the write's `try`.

- [ ] **Step 1: Write the failing tests** (append to `test_progress.py`)

```text
class Stop(Exception):
    pass


def _stopping_after(calls: int):
    seen = {"n": 0}

    def check() -> None:
        seen["n"] += 1
        if seen["n"] > calls:
            raise Stop

    return check


def test_every_update_asks_the_check_even_when_throttled() -> None:
    """The flag is cheap to read; throttling is for the write, not the check."""
    writes: list[tuple[str, float | None]] = []
    report = ProgressReporter(
        "job_1",
        write=lambda s, p: writes.append((s, p)),
        clock=lambda: 0.0,
        check=_stopping_after(2),
    )
    report.stage("transcribing")
    report.update(0.001)
    with pytest.raises(Stop):
        report.update(0.002)


def test_entering_a_stage_asks_the_check() -> None:
    report = ProgressReporter("job_1", write=lambda s, p: None, check=_stopping_after(0))
    with pytest.raises(Stop):
        report.stage("masking")


def test_a_stop_is_not_swallowed_like_a_failed_write() -> None:
    """Write failures are logged and dropped; a stop must reach the task."""

    def broken_write(stage: str, progress: float | None) -> None:
        raise RuntimeError("db down")

    report = ProgressReporter("job_1", write=broken_write, check=_stopping_after(1))
    report.stage("decoding")
    with pytest.raises(Stop):
        report.stage("transcribing")
```

Make sure `import pytest` and `from autune_audio.progress import ProgressReporter` are at the top of the file (they are likely there already).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest modules/audio/tests/unit/test_progress.py -q`
Expected: FAIL — `TypeError: ProgressReporter.__init__() got an unexpected keyword argument 'check'`.

- [ ] **Step 3: Implement**

In `ProgressReporter.__init__`, add the keyword parameter `check: Callable[[], None] | None = None` after `min_step`, and store `self._check = check or (lambda: None)`.

Change `stage` and `update`:

```text
    def stage(self, name: str) -> None:
        """Enter a step. Written immediately, at 0. Asks ``check`` first, so a
        cancelled job stops before it starts the next step."""
        self._check()
        self._stage = name
        self._emit(0.0)

    def update(self, fraction: float) -> None:
        """How far through the current step, 0..1. Throttled -- the write,
        not the check: a cancel is heard at the next callback, not the next
        write."""
        self._check()
        if self._stage is None:
            return
        ...  # the rest unchanged
```

Add one sentence to the module docstring's "Never fails the job" bullet: "``check`` is the exception: it is how a cancel stops the job, and it raises outside the write's ``try``."

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest modules/audio/tests/unit/test_progress.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add modules/audio/src/autune_audio/progress.py modules/audio/tests/unit/test_progress.py
git commit -m "feat(audio): progress callbacks ask whether the job should stop"
```

---

### Task 4: The worker stops when told, and the fence

**Files:**
- Modify: `modules/audio/src/autune_audio/service.py` (new `lock_running_job`, next to `mark_complete`)
- Modify: `modules/audio/src/autune_audio/tasks.py` (`process_recording`, imports)
- Test: `modules/audio/tests/integration/test_tasks.py` (`pipeline` fixture + new tests)

**Interfaces:**
- Consumes: `JobGuard`, `JobStopped`, `beat_with` (Task 2); `ProgressReporter(check=...)` (Task 3); `AudioSettings.heartbeat_interval_s` (Task 1).
- Produces: `service.lock_running_job(session: Session, *, job_id: str) -> None` — locks the meeting row, then the job row, `FOR UPDATE`; raises `JobStopped(status)` unless the job is `running`. `tasks.JobGuard` is the name tests patch.

- [ ] **Step 1: Make the `pipeline` fixture give the task a guard on the test's session**

The task's own `session_scope` is replaced by the fixture, but the guard's default beat opens a real one, which would see no committed row and stop every job. In `test_tasks.py`, add `from autune_audio.job_guard import JobGuard, beat_with` to the imports, and inside the `pipeline` fixture, before `yield state`:

```text
    guards: list[JobGuard] = []

    def guard_on_test_session(job_id: str, *, interval_s: float) -> JobGuard:
        # Interval far beyond the test: only the beat on entry and the
        # explicit ``poll()`` calls below happen, all on this thread.
        guard = JobGuard(job_id, interval_s=3600, beat=lambda: beat_with(db_session, job_id))
        guards.append(guard)
        return guard

    monkeypatch.setattr(tasks, "JobGuard", guard_on_test_session)
    state["guards"] = guards
```

Run: `uv run pytest modules/audio/tests/integration/test_tasks.py -q`
Expected: FAIL — `AttributeError: <module 'autune_audio.tasks'> has no attribute 'JobGuard'` (monkeypatch refuses a missing name). That is the first failing test for this task.

- [ ] **Step 2: Write the failing behaviour tests** (append to `test_tasks.py`)

```text
def _cancel_during(pipeline: dict, db_session: Session, job: str, status: str) -> None:
    """Make the fake recogniser flip the job, then hear it, mid-pass."""
    original = pipeline["transcription"]

    def transcribe(waveform: Waveform, *, on_progress=None, **kw: object) -> Transcription:
        pipeline["steps"].append("transcribe")
        db_session.get(TranscriptionJob, job).status = status
        db_session.flush()
        pipeline["guards"][0].poll()
        if on_progress is not None:
            on_progress(0.5)
        return original

    pipeline["transcribe"] = transcribe


@pytest.fixture
def interruptible(monkeypatch: pytest.MonkeyPatch, pipeline: dict) -> dict:
    """``transcribe`` that defers to ``pipeline["transcribe"]`` when set."""

    def transcribe(waveform: Waveform, **kw: object) -> Transcription:
        if "transcribe" in pipeline:
            return pipeline["transcribe"](waveform, **kw)
        pipeline["steps"].append("transcribe")
        return pipeline["transcription"]

    monkeypatch.setattr(tasks, "transcribe", transcribe)
    return pipeline


def test_the_worker_writes_a_heartbeat_when_it_claims_the_job(
    pipeline: dict, db_session: Session, job: str
) -> None:
    tasks.process_recording(job)

    assert db_session.get(TranscriptionJob, job).heartbeat_at is not None


def test_a_cancel_during_recognition_stops_before_anything_is_written(
    interruptible: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    published: list[tuple[str, dict]],
) -> None:
    # The cancel route sets the meeting failed in the same transaction.
    db_session.get(Meeting, meeting).status = "failed"
    _cancel_during(interruptible, db_session, job, "cancelled")

    tasks.process_recording(job)

    assert published == []
    assert not recording.exists()
    assert db_session.scalars(sa.select(Utterance).where(Utterance.meeting_id == meeting)).all() == []
    assert db_session.get(TranscriptionJob, job).status == "cancelled"
    assert db_session.get(Meeting, meeting).status == "failed"


def test_a_superseded_run_leaves_the_restarted_meeting_analyzing(
    interruptible: dict,
    db_session: Session,
    job: str,
    meeting: str,
    published: list[tuple[str, dict]],
) -> None:
    """A restart supersedes the old attempt while the meeting stays
    ``analyzing`` for the new one. The old run must not fail it."""
    _cancel_during(interruptible, db_session, job, "superseded")

    tasks.process_recording(job)

    assert published == []
    assert db_session.get(Meeting, meeting).status == "analyzing"
    assert db_session.get(TranscriptionJob, job).status == "superseded"


def test_a_cancel_after_the_last_check_is_stopped_by_the_fence(
    pipeline: dict,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    job: str,
    meeting: str,
    published: list[tuple[str, dict]],
) -> None:
    """The cancel commits between ``stage("saving")`` and the write. The
    fence's own read under lock is what stops it."""
    real_stage = tasks.ProgressReporter.stage

    def stage(self, name: str) -> None:
        real_stage(self, name)
        if name == "saving":
            db_session.get(TranscriptionJob, job).status = "cancelled"
            db_session.get(Meeting, meeting).status = "failed"
            db_session.flush()

    monkeypatch.setattr(tasks.ProgressReporter, "stage", stage)

    tasks.process_recording(job)

    assert published == []
    assert db_session.scalars(sa.select(Utterance).where(Utterance.meeting_id == meeting)).all() == []
    assert db_session.get(Meeting, meeting).status == "failed"
```

Note on the fence test: the fixture's `Scope` flushes rather than rolls back, so "nothing written" holds only because the fence raises *before* `persist_transcript`. That is the order the implementation must have.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest modules/audio/tests/integration/test_tasks.py -q`
Expected: FAIL (the `JobGuard` attribute error from Step 1).

- [ ] **Step 4: Implement `lock_running_job`**

In `service.py`, import `from .job_guard import JobStopped` and add after `mark_complete`:

```text
def lock_running_job(session: Session, *, job_id: str) -> None:
    """The fence in front of the transcript write: this attempt is still the
    current one, and stays so until the transaction ends.

    The last ``check()`` and the write are not one step, and a cancel or a
    restart can commit between them. Reading the status under lock closes
    that window: whichever transaction locks first wins, and the other sees
    its result. **Meeting row first, then the job row** -- the order
    ``start_transcription``, ``cancel_transcription`` and
    ``restart_transcription`` take them in, so none of them can deadlock
    against this.
    """
    meeting_id = session.scalar(
        sa.select(TranscriptionJob.meeting_id).where(TranscriptionJob.id == job_id)
    )
    if meeting_id is None:
        raise JobStopped(None)
    session.get(Meeting, meeting_id, with_for_update=True)
    status = session.scalar(
        sa.select(TranscriptionJob.status)
        .where(TranscriptionJob.id == job_id)
        .with_for_update()
    )
    if status != "running":
        raise JobStopped(status)
```

- [ ] **Step 5: Wire the guard into `process_recording`**

In `tasks.py` add `from .job_guard import JobGuard, JobStopped`. Replace the block from `report = ProgressReporter(job_id)` through the end of the `except Exception` handler with:

```text
    with JobGuard(job_id, interval_s=settings.heartbeat_interval_s) as guard:
        report = ProgressReporter(job_id, check=guard.check)
        try:
            with adopt(upload_path(job_id, settings)) as recording:
                ...  # unchanged: resolve_device, decode, transcribe, diarize, vectors
            ...      # unchanged: detect_repetition, report.stage("masking"), masking
            report.stage("saving")
            with session_scope() as session:
                service.lock_running_job(session, job_id=job_id)
                persist_transcript(...)  # unchanged from here
                ...
        except JobStopped as stopped:
            # Cancelled or superseded: whoever changed the status also set the
            # meeting where it belongs. ``mark_failed`` here would undo a
            # restart's ``analyzing``. ``adopt`` has already deleted the file.
            log.info(
                "audio_process_stopped",
                meeting_id=meeting_id,
                job_id=job_id,
                status=stopped.status,
            )
            return
        except Exception as error:
            ...  # unchanged
```

Keep the publish / `mark_published` block after the `with JobGuard` block exactly as it is: once `mark_complete` has committed, the meeting is `complete` and neither route accepts it, so the guard has nothing left to hear.

`settings` is the `settings = get_settings()` already at the top of `process_recording` (tasks.py:137).

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest modules/audio/tests/integration/test_tasks.py modules/audio/tests/unit -q`
Expected: all pass, including every test that existed before.

- [ ] **Step 7: Commit**

```bash
git add modules/audio/src/autune_audio/service.py modules/audio/src/autune_audio/tasks.py modules/audio/tests/integration/test_tasks.py
git commit -m "feat(audio): a cancelled or superseded run stops, and writes nothing"
```

---

### Task 5: The six hours run from the upload

**Files:**
- Modify: `modules/audio/src/autune_audio/service.py` (`sweep_orphans`, the `queued`/`running` branch and its docstring)
- Test: `modules/audio/tests/integration/test_tasks.py` (next to the other sweep tests)

**Interfaces:**
- Consumes: nothing new.
- Produces: `sweep_orphans` treats a `queued`/`running` job's file as abandoned when **either** the job or the file is older than `orphan_after_hours`; `cancelled` files are collected immediately (already true through the existing "attempt is over" branch; pinned here).

- [ ] **Step 1: Write the failing tests**

```text
def test_a_restarted_attempt_does_not_get_six_more_hours(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    settings: AudioSettings,
) -> None:
    """A restart makes a new job for an old file. The clock is the file's."""
    restarted = _job(db_session, meeting, "running")
    old_file = _upload(settings, restarted)
    hours_ago = datetime.now(tz=UTC) - timedelta(hours=settings.orphan_after_hours + 1)
    os.utime(old_file, (hours_ago.timestamp(), hours_ago.timestamp()))

    tasks.process_recording(job)

    assert not old_file.exists()
    assert db_session.get(TranscriptionJob, restarted).status == "failed"


def test_the_sweep_collects_a_cancelled_attempts_file(
    pipeline: dict,
    db_session: Session,
    job: str,
    meeting: str,
    recording: Path,
    settings: AudioSettings,
) -> None:
    cancelled = _job(db_session, meeting, "cancelled")
    left = _upload(settings, cancelled)

    tasks.process_recording(job)

    assert not left.exists()
```

Add `import os` to the test file's imports.

- [ ] **Step 2: Run tests to verify the first fails**

Run: `uv run pytest modules/audio/tests/integration/test_tasks.py -k "six_more_hours or cancelled_attempts_file" -q`
Expected: `test_a_restarted_attempt_does_not_get_six_more_hours` FAILS (file still exists); the cancelled one passes.

- [ ] **Step 3: Implement**

In `sweep_orphans`, change:

```text
        elif job.status in ("queued", "running"):
            if job.created_at >= cutoff:
                continue
```

to:

```text
        elif job.status in ("queued", "running"):
            # The earlier of the job and the file: a restart makes a new job
            # for an old upload, and must not grant it another window
            # (invariant 11). A rename keeps mtime.
            if job.created_at >= cutoff and not stale(path):
                continue
```

In the docstring paragraph "A ``queued`` or ``running`` job is left alone until it is older than ``orphan_after_hours``", replace it with "...until it, or its file, is older than ``orphan_after_hours`` -- the file's age counts because a restart (``restart_transcription``) gives an old upload a new job." and add ``cancelled`` to "``done``, ``failed`` or ``superseded`` means the attempt is over".

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest modules/audio/tests/integration/test_tasks.py -q`
Expected: all pass (including `test_the_sweep_leaves_a_live_attempt_alone`: its file is fresh).

- [ ] **Step 5: Commit**

```bash
git add modules/audio/src/autune_audio/service.py modules/audio/tests/integration/test_tasks.py
git commit -m "fix(audio): an upload's six hours run from the upload, not the latest attempt"
```

---

### Task 6: Cancel

**Files:**
- Modify: `modules/audio/src/autune_audio/service.py` (new section after `mark_published`)
- Modify: `modules/audio/src/autune_audio/router.py` (new route after `upload_recording`)
- Test: `modules/audio/tests/integration/test_cancel_restart.py`

**Interfaces:**
- Consumes: `TranscriptionJob.heartbeat_at`, `AudioSettings.stall_after_s` (Task 1); `storage.upload_path`, `storage.delete_orphan` (existing).
- Produces:
  - `class NothingToCancelError(ConflictError)`, `code = "nothing_to_cancel"`.
  - `service.latest_job(session, *, meeting_id: str, lock: bool = False) -> TranscriptionJob | None` — newest by `created_at`.
  - `service.is_stalled(job: TranscriptionJob, *, settings: AudioSettings, now: datetime) -> bool`.
  - `service.cancel_transcription(session, *, meeting_id: str, user: User, settings: AudioSettings) -> Meeting`.
  - Route `POST /meetings/{meeting_id}/transcription/cancel` → 200 `MeetingState`.

- [ ] **Step 1: Write the failing tests** (append to `test_cancel_restart.py`)

Add imports at the top of the file:

```text
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from autune_audio import service
from autune_audio.config import AudioSettings
from autune_audio.router import router
from autune_core import AutuneError, TeamMember, User, get_session
from autune_core.auth import current_user
from autune_core.errors import PermissionDeniedError
```

Fixtures and tests:

```text
@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="member@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


@pytest.fixture
def outsider(db_session: Session) -> User:
    user = User(email="outsider@example.com", display_name="남")
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AudioSettings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = AudioSettings(temp_dir=str(scratch))
    monkeypatch.setattr("autune_audio.router.get_audio_settings", lambda: fake)
    return fake


@pytest.fixture
def analyzing(db_session: Session, meeting: str) -> str:
    db_session.get(Meeting, meeting).status = "analyzing"
    db_session.flush()
    return meeting


def _upload(settings: AudioSettings, job_id: str) -> Path:
    path = Path(settings.temp_dir) / f"{job_id}.upload"
    path.write_bytes(b"raw audio stand-in")
    return path


@pytest.fixture
def client(db_session: Session, member: User) -> TestClient:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/audio")
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[current_user] = lambda: member
    return TestClient(app)


# --- cancel -------------------------------------------------------------------


def test_cancelling_a_running_job_fails_the_meeting_and_leaves_the_file_to_its_worker(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(seconds=5))
    upload = _upload(settings, job.id)

    meeting = service.cancel_transcription(
        db_session, meeting_id=analyzing, user=member, settings=settings
    )

    assert meeting.status == "failed"
    assert job.status == "cancelled"
    assert job.finished_at is not None
    assert upload.exists()  # the live worker's adopt() deletes it


def test_cancelling_a_queued_job_leaves_the_file_to_the_claim(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "queued")
    upload = _upload(settings, job.id)

    service.cancel_transcription(db_session, meeting_id=analyzing, user=member, settings=settings)

    assert job.status == "cancelled"
    assert upload.exists()  # claim_job declines it with owns_file=True


def test_cancelling_a_stalled_job_deletes_the_file_at_once(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    """Its owner is gone; the API takes ownership rather than wait an hour
    for the sweep."""
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    upload = _upload(settings, job.id)

    service.cancel_transcription(db_session, meeting_id=analyzing, user=member, settings=settings)

    assert not upload.exists()


@pytest.mark.parametrize("meeting_status", ["complete", "failed", "scheduled"])
def test_there_is_nothing_to_cancel_outside_analyzing(
    db_session: Session, meeting: str, member: User, settings: AudioSettings, meeting_status: str
) -> None:
    db_session.get(Meeting, meeting).status = meeting_status
    _job(db_session, meeting, "running")

    with pytest.raises(service.NothingToCancelError):
        service.cancel_transcription(db_session, meeting_id=meeting, user=member, settings=settings)


def test_a_second_cancel_is_refused(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running")
    service.cancel_transcription(db_session, meeting_id=analyzing, user=member, settings=settings)

    with pytest.raises(service.NothingToCancelError):
        service.cancel_transcription(
            db_session, meeting_id=analyzing, user=member, settings=settings
        )


def test_an_outsider_cannot_cancel(
    db_session: Session, analyzing: str, outsider: User, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running")

    with pytest.raises(PermissionDeniedError):
        service.cancel_transcription(
            db_session, meeting_id=analyzing, user=outsider, settings=settings
        )


def test_the_cancel_route_answers_with_the_meetings_state(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running")

    response = client.post(f"/api/audio/meetings/{analyzing}/transcription/cancel")

    assert response.status_code == 200
    assert response.json() == {"meeting_id": analyzing, "status": "failed"}


def test_the_cancel_route_says_why_it_refused(
    client: TestClient, db_session: Session, meeting: str, settings: AudioSettings
) -> None:
    response = client.post(f"/api/audio/meetings/{meeting}/transcription/cancel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "nothing_to_cancel"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest modules/audio/tests/integration/test_cancel_restart.py -q`
Expected: FAIL — `AttributeError: module 'autune_audio.service' has no attribute 'cancel_transcription'`.

- [ ] **Step 3: Implement the service**

In `service.py` (after `mark_published`):

```text
# --------------------------------------------------------------------------- #
# Cancel and restart (spec 2026-10-02)
# --------------------------------------------------------------------------- #


class NothingToCancelError(ConflictError):
    code = "nothing_to_cancel"


def latest_job(
    session: Session, *, meeting_id: str, lock: bool = False
) -> TranscriptionJob | None:
    """The meeting's newest attempt -- the one a restart or a re-upload made
    last, and so the one the screen and both routes are about."""
    query = (
        sa.select(TranscriptionJob)
        .where(TranscriptionJob.meeting_id == meeting_id)
        .order_by(TranscriptionJob.created_at.desc())
        .limit(1)
    )
    if lock:
        query = query.with_for_update()
    return session.scalar(query)


def is_stalled(job: TranscriptionJob, *, settings: AudioSettings, now: datetime) -> bool:
    """A ``running`` job whose worker has stopped writing its heartbeat.

    Not progress: diarization and a remote STT call can report none for
    minutes on a healthy run. A job claimed before ``heartbeat_at`` existed
    falls back to ``created_at``."""
    if job.status != "running":
        return False
    last = job.heartbeat_at or job.created_at
    return last < now - timedelta(seconds=settings.stall_after_s)


def cancel_transcription(
    session: Session, *, meeting_id: str, user: User, settings: AudioSettings
) -> Meeting:
    """Stop the meeting's transcription and leave it ``failed``, which accepts
    a new upload.

    **Locks the meeting, then the job** -- the order ``lock_running_job``
    takes them in, so a cancel and the transcript write serialise instead of
    deadlocking: if the write committed first the meeting is ``complete`` and
    this refuses; if this commits first the worker's fence stops the write.

    **Who deletes the upload.** A live worker does, through ``adopt``, once
    its guard hears the status; a ``queued`` job's file goes when
    ``claim_job`` declines it. A stalled job's owner is gone, so this takes
    ownership and deletes it now (privacy.md section 1). The sweep is the
    backstop for all three.
    """
    meeting = session.get(Meeting, meeting_id, with_for_update=True)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=user.id, team_id=meeting.team_id)
    job = latest_job(session, meeting_id=meeting_id, lock=True)
    if meeting.status != "analyzing" or job is None or job.status not in ("queued", "running"):
        raise NothingToCancelError(f"meeting {meeting_id} has no transcription in progress")

    now = datetime.now(tz=UTC)
    stalled = is_stalled(job, settings=settings, now=now)
    job.status = "cancelled"
    job.finished_at = now
    meeting.status = "failed"
    session.flush()
    if stalled:
        storage.delete_orphan(storage.upload_path(job.id, settings))
    log.info(
        "audio_transcription_cancelled", meeting_id=meeting_id, job_id=job.id, stalled=stalled
    )
    return meeting
```

- [ ] **Step 4: Implement the route**

In `router.py`, after `upload_recording`:

```text
@router.post("/meetings/{meeting_id}/transcription/cancel", response_model=MeetingState)
def cancel_transcription(meeting_id: str, user: CurrentUser, session: SessionDep) -> MeetingState:
    """Stop the meeting's transcription (S12 "처리 취소"). The meeting is
    ``failed`` on return and accepts a new upload; the worker stops within one
    heartbeat. 409 ``nothing_to_cancel`` when nothing is running."""
    meeting = service.cancel_transcription(
        session, meeting_id=meeting_id, user=user, settings=get_audio_settings()
    )
    session.commit()
    return MeetingState(meeting_id=meeting.id, status=meeting.status)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest modules/audio/tests/integration/test_cancel_restart.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add modules/audio/src/autune_audio/service.py modules/audio/src/autune_audio/router.py modules/audio/tests/integration/test_cancel_restart.py
git commit -m "feat(audio): a transcription in progress can be cancelled"
```

---

### Task 7: Restart

**Files:**
- Modify: `modules/audio/src/autune_audio/service.py` (after `cancel_transcription`)
- Modify: `modules/audio/src/autune_audio/router.py` (after the cancel route)
- Test: `modules/audio/tests/integration/test_cancel_restart.py`

**Interfaces:**
- Consumes: `latest_job`, `is_stalled` (Task 6); `storage.upload_path`, `storage.delete_orphan`; `enqueue_process_recording`; `EnqueueFailedError` (router).
- Produces:
  - `class NotStalledError(ConflictError)`, `code = "not_stalled"`; `class RecordingGoneError(ConflictError)`, `code = "recording_gone"`.
  - `service.recording_restartable(job_id: str, *, settings: AudioSettings, now: datetime) -> bool`.
  - `service.restart_transcription(session, *, meeting_id: str, user: User, settings: AudioSettings) -> TranscriptionJob` (the new job).
  - Route `POST /meetings/{meeting_id}/transcription/restart` → 202 `MeetingState`.

- [ ] **Step 1: Write the failing tests**

Add `import os` to the imports, and the broker stand-in from `test_upload.py`:

```text
class Enqueued:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list[object]]] = []
        self.explode = False

    def send_task(self, name: str, args: list[object], **_: object) -> None:
        if self.explode:
            raise RuntimeError("broker is unreachable")
        self.sent.append((name, args))


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> Enqueued:
    fake = Enqueued()
    monkeypatch.setattr("autune_audio.enqueue.current_app", fake)
    return fake


# --- restart ------------------------------------------------------------------


def test_a_stalled_job_restarts_from_the_same_upload(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    upload = _upload(settings, old.id)

    new = service.restart_transcription(
        db_session, meeting_id=analyzing, user=member, settings=settings
    )

    assert old.status == "superseded"
    assert old.finished_at is not None
    assert new.status == "queued"
    assert new.meeting_id == analyzing
    assert not upload.exists()
    assert (Path(settings.temp_dir) / f"{new.id}.upload").read_bytes() == b"raw audio stand-in"
    assert db_session.get(Meeting, analyzing).status == "analyzing"


def test_a_job_with_a_live_heartbeat_is_not_restarted(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    """Restarting a live run would run one file twice."""
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(seconds=10))
    _upload(settings, old.id)

    with pytest.raises(service.NotStalledError):
        service.restart_transcription(
            db_session, meeting_id=analyzing, user=member, settings=settings
        )


def test_a_meeting_already_complete_is_not_restarted(
    db_session: Session, meeting: str, member: User, settings: AudioSettings
) -> None:
    """A worker that died between mark_complete and mark_published leaves the
    job running and stale, and the meeting delivered."""
    db_session.get(Meeting, meeting).status = "complete"
    old = _job(db_session, meeting, "running", heartbeat_age=timedelta(minutes=10))
    _upload(settings, old.id)

    with pytest.raises(service.NotStalledError):
        service.restart_transcription(db_session, meeting_id=meeting, user=member, settings=settings)


def test_a_stalled_job_without_its_file_cannot_restart(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))

    with pytest.raises(service.RecordingGoneError):
        service.restart_transcription(
            db_session, meeting_id=analyzing, user=member, settings=settings
        )


def test_an_upload_past_its_six_hours_cannot_restart(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    upload = _upload(settings, old.id)
    then = (datetime.now(tz=UTC) - timedelta(hours=settings.orphan_after_hours + 1)).timestamp()
    os.utime(upload, (then, then))

    with pytest.raises(service.RecordingGoneError):
        service.restart_transcription(
            db_session, meeting_id=analyzing, user=member, settings=settings
        )


def test_a_second_restart_is_refused(
    db_session: Session, analyzing: str, member: User, settings: AudioSettings
) -> None:
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    _upload(settings, old.id)
    service.restart_transcription(db_session, meeting_id=analyzing, user=member, settings=settings)

    with pytest.raises(service.NotStalledError):
        service.restart_transcription(
            db_session, meeting_id=analyzing, user=member, settings=settings
        )


def test_the_restart_route_queues_the_new_job(
    client: TestClient,
    db_session: Session,
    analyzing: str,
    settings: AudioSettings,
    broker: Enqueued,
) -> None:
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    _upload(settings, old.id)

    response = client.post(f"/api/audio/meetings/{analyzing}/transcription/restart")

    assert response.status_code == 202
    assert response.json() == {"meeting_id": analyzing, "status": "analyzing"}
    [(name, [job_id])] = broker.sent
    assert name == "autune.audio.process_recording"
    assert job_id != old.id
    assert db_session.get(TranscriptionJob, job_id).status == "queued"


def test_a_restart_the_broker_refuses_fails_the_meeting_and_deletes_the_file(
    client: TestClient,
    db_session: Session,
    analyzing: str,
    settings: AudioSettings,
    broker: Enqueued,
) -> None:
    old = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    _upload(settings, old.id)
    broker.explode = True

    response = client.post(f"/api/audio/meetings/{analyzing}/transcription/restart")

    assert response.status_code == 500
    assert db_session.get(Meeting, analyzing).status == "failed"
    assert list(Path(settings.temp_dir).iterdir()) == []


def test_the_restart_route_says_the_recording_is_gone(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))

    response = client.post(f"/api/audio/meetings/{analyzing}/transcription/restart")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "recording_gone"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest modules/audio/tests/integration/test_cancel_restart.py -k restart -q`
Expected: FAIL — `AttributeError: module 'autune_audio.service' has no attribute 'restart_transcription'`.

- [ ] **Step 3: Implement the service**

After `cancel_transcription`:

```text
class NotStalledError(ConflictError):
    code = "not_stalled"


class RecordingGoneError(ConflictError):
    code = "recording_gone"


def recording_restartable(job_id: str, *, settings: AudioSettings, now: datetime) -> bool:
    """The attempt's upload is still on disk and inside its six hours.

    By mtime, which a rename keeps: the window runs from the upload, so a
    restart cannot extend it (``sweep_orphans`` measures the same way)."""
    path = storage.upload_path(job_id, settings)
    if not path.exists():
        return False
    uploaded = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return uploaded >= now - timedelta(hours=settings.orphan_after_hours)


def restart_transcription(
    session: Session, *, meeting_id: str, user: User, settings: AudioSettings
) -> TranscriptionJob:
    """Run a stalled meeting again from the upload still on the server.

    Only a stalled job: a live one would run the file twice, and the first to
    finish would delete it under the other. Only an ``analyzing`` meeting: a
    worker that died after ``mark_complete`` leaves a stale ``running`` job on
    a meeting already delivered.

    The same steps as an upload's claim, with the file renamed instead of
    written: the old attempt is ``superseded`` (if its worker was only slow,
    its guard hears that and stops; its ``adopt`` then finds no file to
    delete), a new job is ``queued``, and the file takes the new job's name.
    The caller commits and enqueues, as ``upload_recording`` does. Should the
    commit fail after the rename, the file is one whose job name no row knows,
    which the sweep collects past its mtime.
    """
    meeting = session.get(Meeting, meeting_id, with_for_update=True)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=user.id, team_id=meeting.team_id)
    old = latest_job(session, meeting_id=meeting_id, lock=True)
    now = datetime.now(tz=UTC)
    if (
        meeting.status != "analyzing"
        or old is None
        or not is_stalled(old, settings=settings, now=now)
    ):
        raise NotStalledError(f"meeting {meeting_id} has no stalled transcription")
    if not recording_restartable(old.id, settings=settings, now=now):
        raise RecordingGoneError(f"the recording for meeting {meeting_id} is no longer on the server")

    old.status = "superseded"
    old.finished_at = now
    new = TranscriptionJob(meeting_id=meeting_id, status="queued")
    session.add(new)
    session.flush()
    storage.upload_path(old.id, settings).rename(storage.upload_path(new.id, settings))
    log.info("audio_transcription_restarted", meeting_id=meeting_id, old_job_id=old.id, job_id=new.id)
    return new
```

- [ ] **Step 4: Implement the route**

In `router.py`, add `from . import storage` to the `from . import ...` line, and after the cancel route:

```text
@router.post(
    "/meetings/{meeting_id}/transcription/restart",
    response_model=MeetingState,
    status_code=status.HTTP_202_ACCEPTED,
)
def restart_transcription(meeting_id: str, user: CurrentUser, session: SessionDep) -> MeetingState:
    """Run a stalled meeting again from its upload (S12 "다시 시작").

    409 ``not_stalled`` while the worker is alive, ``recording_gone`` when the
    upload is no longer on the server. Commit before the enqueue and fail the
    meeting if the broker refuses, exactly as ``upload_recording`` does; here
    there is no ``handover`` block to delete the file, so this does."""
    settings = get_audio_settings()
    job = service.restart_transcription(
        session, meeting_id=meeting_id, user=user, settings=settings
    )
    session.commit()
    try:
        enqueue_process_recording(job.id)
    except Exception as error:
        log.warning("audio_enqueue_failed", job_id=job.id, error=type(error).__name__)
        service.mark_failed(session, job_id=job.id)
        session.commit()
        storage.delete_orphan(storage.upload_path(job.id, settings))
        raise EnqueueFailedError() from error
    return MeetingState(meeting_id=job.meeting_id, status=job.meeting.status)
```

If `router.py` names its logger differently than `log`, use that name.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest modules/audio/tests/integration/test_cancel_restart.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add modules/audio/src/autune_audio/service.py modules/audio/src/autune_audio/router.py modules/audio/tests/integration/test_cancel_restart.py
git commit -m "feat(audio): a stalled transcription restarts from the upload still on the server"
```

---

### Task 8: The flags on `GET /meetings/{id}`

**Files:**
- Modify: `modules/audio/src/autune_audio/schemas.py` (`MeetingDetail`)
- Modify: `modules/audio/src/autune_audio/service.py` (after `restart_transcription`)
- Modify: `modules/audio/src/autune_audio/router.py` (`get_meeting`)
- Test: `modules/audio/tests/integration/test_cancel_restart.py`

**Interfaces:**
- Consumes: `latest_job`, `is_stalled`, `recording_restartable` (Tasks 6–7).
- Produces:
  - `class TranscriptionControls(NamedTuple)`: `stalled: bool`, `restartable: bool`, `cancellable: bool`, `cancelled: bool`.
  - `service.transcription_controls(session, *, meeting: Meeting, settings: AudioSettings) -> TranscriptionControls`.
  - `MeetingDetail` gains `stalled`, `restartable`, `cancellable`, `cancelled`, all `bool = False`.

- [ ] **Step 1: Write the failing tests**

```text
# --- flags --------------------------------------------------------------------


def _detail(client: TestClient, meeting_id: str) -> dict:
    response = client.get(f"/api/audio/meetings/{meeting_id}")
    assert response.status_code == 200
    return response.json()


def test_a_healthy_run_can_be_cancelled_but_not_restarted(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(seconds=5))
    _upload(settings, job.id)

    body = _detail(client, analyzing)

    assert (body["cancellable"], body["stalled"], body["restartable"], body["cancelled"]) == (
        True, False, False, False,
    )


def test_a_stalled_run_with_its_upload_is_restartable(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    job = _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))
    _upload(settings, job.id)

    body = _detail(client, analyzing)

    assert (body["stalled"], body["restartable"], body["cancellable"]) == (True, True, True)


def test_a_stalled_run_without_its_upload_is_stalled_but_not_restartable(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    _job(db_session, analyzing, "running", heartbeat_age=timedelta(minutes=10))

    body = _detail(client, analyzing)

    assert (body["stalled"], body["restartable"]) == (True, False)


def test_a_cancelled_meeting_says_so(
    client: TestClient, db_session: Session, meeting: str, settings: AudioSettings
) -> None:
    db_session.get(Meeting, meeting).status = "failed"
    _job(db_session, meeting, "cancelled")

    body = _detail(client, meeting)

    assert body["cancelled"] is True
    assert body["cancellable"] is False


def test_a_meeting_that_failed_on_its_own_is_not_called_cancelled(
    client: TestClient, db_session: Session, meeting: str, settings: AudioSettings
) -> None:
    db_session.get(Meeting, meeting).status = "failed"
    _job(db_session, meeting, "failed")

    assert _detail(client, meeting)["cancelled"] is False


def test_a_queued_job_is_never_stalled(
    client: TestClient, db_session: Session, analyzing: str, settings: AudioSettings
) -> None:
    """A long queue and a lost message look the same from here."""
    _job(db_session, analyzing, "queued", age=timedelta(hours=1))

    body = _detail(client, analyzing)

    assert (body["stalled"], body["cancellable"]) == (False, True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest modules/audio/tests/integration/test_cancel_restart.py -k "flags or healthy or stalled_run or cancelled_meeting or on_its_own or never_stalled" -q`
Expected: FAIL — `KeyError: 'cancellable'`.

- [ ] **Step 3: Implement**

`schemas.py`, in `MeetingDetail` after `stage_progress`:

```text
    stalled: bool = False
    """The running attempt's worker stopped writing its heartbeat
    (``service.is_stalled``). The screen offers 다시 시작 or 취소."""
    restartable: bool = False
    """``stalled`` and its upload is still on the server, inside six hours."""
    cancellable: bool = False
    """``analyzing`` with an attempt ``queued`` or ``running``."""
    cancelled: bool = False
    """``failed`` because a person cancelled, not because something broke."""
```

`service.py`, after `restart_transcription`:

```text
class TranscriptionControls(NamedTuple):
    stalled: bool
    restartable: bool
    cancellable: bool
    cancelled: bool


def transcription_controls(
    session: Session, *, meeting: Meeting, settings: AudioSettings
) -> TranscriptionControls:
    """What S12 may offer for this meeting, decided here so the screen draws
    buttons from one rule. Uses the same checks the two routes enforce."""
    job = latest_job(session, meeting_id=meeting.id)
    if job is None:
        return TranscriptionControls(False, False, False, False)
    now = datetime.now(tz=UTC)
    analyzing = meeting.status == "analyzing"
    stalled = analyzing and is_stalled(job, settings=settings, now=now)
    return TranscriptionControls(
        stalled=stalled,
        restartable=stalled and recording_restartable(job.id, settings=settings, now=now),
        cancellable=analyzing and job.status in ("queued", "running"),
        cancelled=meeting.status == "failed" and job.status == "cancelled",
    )
```

`router.py`, in `get_meeting`, after `stage, stage_progress = ...`:

```text
    controls = service.transcription_controls(
        session, meeting=meeting, settings=get_audio_settings()
    )
```

and pass `**controls._asdict()` into `MeetingDetail(...)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest modules/audio/tests -q`
Expected: all pass, including `test_meeting_read.py`.

- [ ] **Step 5: Lint and types**

Run: `uv run ruff check modules/audio && uv run ruff format --check modules/audio && uv run mypy modules/audio/src`
Expected: no errors. Fix any before committing.

- [ ] **Step 6: Commit**

```bash
git add modules/audio/src/autune_audio/schemas.py modules/audio/src/autune_audio/service.py modules/audio/src/autune_audio/router.py modules/audio/tests/integration/test_cancel_restart.py
git commit -m "feat(audio): the meeting says whether it can be cancelled or restarted"
```

---

### Task 9: S12 controls

**Files:**
- Modify: `apps/web/src/features/transcript/types.ts` (`MeetingDetail`)
- Modify: `apps/web/src/features/transcript/api.ts` (after `getMeeting`)
- Create: `apps/web/src/features/transcript/components/TranscriptionControls.tsx`
- Create: `apps/web/src/features/transcript/components/TranscriptionControls.test.tsx`
- Modify: `apps/web/src/features/transcript/components/ProcessingStages.tsx` (mount the controls; failed-paragraph wording)

**Interfaces:**
- Consumes: the four `MeetingDetail` flags and the two routes (Tasks 6–8).
- Produces: `cancelTranscription(meetingId: string): Promise<MeetingState>`, `restartTranscription(meetingId: string): Promise<MeetingState>`; `<TranscriptionControls meeting={MeetingDetail} />`.

The screen needs no reload hook: `useMeeting` keeps polling every 3 s while the meeting is `analyzing`, so a cancel shows as `failed` and a restart as a moving stage list within one poll. The controls show a pending state until then.

- [ ] **Step 1: Types and API**

`types.ts`, add to `MeetingDetail` after `stage_progress`:

```ts
  /** The running attempt's worker stopped writing its heartbeat. */
  stalled: boolean;
  /** `stalled`, and its upload is still on the server. */
  restartable: boolean;
  /** `analyzing` with an attempt queued or running. */
  cancellable: boolean;
  /** `failed` because a person cancelled it. */
  cancelled: boolean;
```

`api.ts`, after `getMeeting` (add `MeetingState` to the type import if the file has it; otherwise use `{ meeting_id: string; status: MeetingStatus }`):

```ts
/** S12 "처리 취소". The meeting is `failed` on return and takes a new upload. */
export const cancelTranscription = (meetingId: string) =>
  api.audio<{ meeting_id: string; status: string }>(
    `/meetings/${meetingId}/transcription/cancel`,
    { method: "POST" },
  );

/** S12 "다시 시작": run a stalled meeting again from the upload still on the server. */
export const restartTranscription = (meetingId: string) =>
  api.audio<{ meeting_id: string; status: string }>(
    `/meetings/${meetingId}/transcription/restart`,
    { method: "POST" },
  );
```

- [ ] **Step 2: Write the failing component tests**

`TranscriptionControls.test.tsx`:

```tsx
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MeetingDetail } from "../types";

const cancelTranscription = vi.fn();
const restartTranscription = vi.fn();
vi.mock("../api", () => ({
  cancelTranscription: (id: string) => cancelTranscription(id),
  restartTranscription: (id: string) => restartTranscription(id),
}));

import { TranscriptionControls } from "./TranscriptionControls";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function meeting(flags: Partial<MeetingDetail>): MeetingDetail {
  return {
    meeting_id: "mtg_1",
    title: "회의",
    status: "analyzing",
    original_audio_deleted: false,
    pii_masked: false,
    team_id: "team_1",
    stage: "transcribing",
    stage_progress: 0.4,
    stalled: false,
    restartable: false,
    cancellable: false,
    cancelled: false,
    ...flags,
  };
}

describe("TranscriptionControls", () => {
  it("draws nothing when there is nothing to do", () => {
    const { container } = render(<TranscriptionControls meeting={meeting({})} />);
    expect(container.innerHTML).toBe("");
  });

  it("asks before cancelling, then cancels", async () => {
    cancelTranscription.mockResolvedValue({ meeting_id: "mtg_1", status: "failed" });
    render(<TranscriptionControls meeting={meeting({ cancellable: true })} />);

    fireEvent.click(screen.getByRole("button", { name: "처리 취소" }));
    expect(cancelTranscription).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "취소하기" }));

    await waitFor(() => expect(cancelTranscription).toHaveBeenCalledWith("mtg_1"));
  });

  it("lets the person back out of the confirmation", () => {
    render(<TranscriptionControls meeting={meeting({ cancellable: true })} />);

    fireEvent.click(screen.getByRole("button", { name: "처리 취소" }));
    fireEvent.click(screen.getByRole("button", { name: "계속 진행" }));

    expect(screen.queryByRole("button", { name: "취소하기" })).toBeNull();
    expect(cancelTranscription).not.toHaveBeenCalled();
  });

  it("offers a restart for a stalled run whose upload is still there", async () => {
    restartTranscription.mockResolvedValue({ meeting_id: "mtg_1", status: "analyzing" });
    render(
      <TranscriptionControls
        meeting={meeting({ stalled: true, restartable: true, cancellable: true })}
      />,
    );

    expect(screen.getByRole("status").textContent).toContain("응답이 없");
    fireEvent.click(screen.getByRole("button", { name: "다시 시작" }));

    await waitFor(() => expect(restartTranscription).toHaveBeenCalledWith("mtg_1"));
  });

  it("says why a stalled run cannot restart, and offers only cancel", () => {
    render(
      <TranscriptionControls
        meeting={meeting({ stalled: true, restartable: false, cancellable: true })}
      />,
    );

    expect(screen.queryByRole("button", { name: "다시 시작" })).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("다시 올려");
    expect(screen.getByRole("button", { name: "처리 취소" })).toBeTruthy();
  });

  it("shows the server's refusal instead of failing silently", async () => {
    cancelTranscription.mockRejectedValue(new Error("이미 끝난 처리입니다"));
    render(<TranscriptionControls meeting={meeting({ cancellable: true })} />);

    fireEvent.click(screen.getByRole("button", { name: "처리 취소" }));
    fireEvent.click(screen.getByRole("button", { name: "취소하기" }));

    expect((await screen.findByRole("alert")).textContent).toContain("이미 끝난 처리입니다");
  });
});
```

Plain matchers only: this repo has no `@testing-library/jest-dom` setup.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd apps/web && pnpm test -- TranscriptionControls`
Expected: FAIL — cannot resolve `./TranscriptionControls`.

- [ ] **Step 4: Implement `TranscriptionControls.tsx`**

```tsx
"use client";

import { useState } from "react";

import { cancelTranscription, restartTranscription } from "../api";
import type { MeetingDetail } from "../types";

/**
 * S12's cancel and restart (spec 2026-10-02, section 4). The S12 mockup has
 * neither, so this uses the screen's existing tokens and nothing new.
 *
 * Which buttons show is the server's decision (`MeetingDetail.stalled`,
 * `restartable`, `cancellable`), so the rule lives in one place. After a
 * press nothing is reloaded here: `useMeeting` polls every three seconds
 * while the meeting is `analyzing`, and the next poll shows the result.
 *
 * Cancel asks in place rather than through `window.confirm`: a browser
 * dialog blocks the page, and the question needs one sentence of context
 * the dialog would lose (the original on the server is deleted).
 */
export function TranscriptionControls({ meeting }: { meeting: MeetingDetail }) {
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!meeting.cancellable && !meeting.stalled) return null;

  const run = async (call: (id: string) => Promise<unknown>) => {
    setPending(true);
    setError(null);
    try {
      await call(meeting.meeting_id);
      setConfirming(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "요청을 처리하지 못했습니다.");
      setPending(false);
    }
  };

  const meta = { fontSize: "var(--text-meta)" } as const;
  const link = "text-[var(--color-accent-default)] disabled:opacity-50";

  return (
    <div className="mt-3 flex flex-col gap-2" style={meta}>
      {meeting.stalled && (
        <p role="status" className="text-[var(--color-ink-strong)]">
          {meeting.restartable
            ? "2분 넘게 처리에 응답이 없어요. 서버가 다시 시작됐을 수 있어요."
            : "서버에 녹음 파일이 남아 있지 않아 다시 시작할 수 없어요. 처리를 취소하고 녹음을 다시 올려 주세요. 실시간으로 녹음한 회의라면 다시 올릴 파일이 없을 수도 있어요."}
        </p>
      )}

      {confirming ? (
        <p className="text-[var(--color-ink-strong)]">
          처리를 취소하면 서버에 있는 원본 녹음이 삭제돼요.{" "}
          <button type="button" className={link} disabled={pending} onClick={() => run(cancelTranscription)}>
            취소하기
          </button>{" "}
          <button
            type="button"
            className="text-[var(--color-ink-muted)]"
            disabled={pending}
            onClick={() => setConfirming(false)}
          >
            계속 진행
          </button>
        </p>
      ) : (
        <p className="flex gap-3">
          {meeting.stalled && meeting.restartable && (
            <button type="button" className={link} disabled={pending} onClick={() => run(restartTranscription)}>
              다시 시작
            </button>
          )}
          {meeting.cancellable && (
            <button
              type="button"
              className="text-[var(--color-ink-muted)] disabled:opacity-50"
              disabled={pending}
              onClick={() => setConfirming(true)}
            >
              처리 취소
            </button>
          )}
        </p>
      )}

      {error !== null && (
        <p role="alert" style={{ color: "var(--color-signal-critical)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Mount it in `ProcessingStages.tsx`**

Import `TranscriptionControls` and render it right after the closing `</ol>`:

```tsx
      <TranscriptionControls meeting={meeting} />
```

In the `meeting.status === "failed"` paragraph, make the sentence depend on `meeting.cancelled`:

```tsx
          <span
            style={{
              color: meeting.cancelled ? "var(--color-ink-strong)" : "var(--color-signal-critical)",
            }}
          >
            {meeting.cancelled
              ? "처리를 취소했어요. 서버에 있던 원본 녹음은 삭제됐어요."
              : "처리에 실패했습니다. 원본 녹음은 삭제되었습니다."}
          </span>{" "}
```

and change `role="alert"` on that paragraph to `role={meeting.cancelled ? "status" : "alert"}` (a cancel the person asked for is not an alarm). Update the component's docstring last paragraph: "`failed` turns the step that was running red, unless the person cancelled it (`meeting.cancelled`); either way the retry is a new upload for the same meeting."

- [ ] **Step 6: Korean copy pass**

Dispatch a Korean-writing subagent with the five new strings (stalled ×2, confirm, cancelled, the error fallback) and the existing S12 sentences for tone, asking only that they not read translated and stay as short as they are. Apply its rewrites to the component and to the test's `toContain` fragments.

- [ ] **Step 7: Run tests, types, lint, build**

Run: `cd apps/web && pnpm test && pnpm run typecheck && pnpm run lint && pnpm run build`
Expected: all succeed.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/features/transcript
git commit -m "feat(transcript): S12 offers cancel, and restart for a stalled transcription"
```

---

### Task 10: Real run, numbers, docs

**Files:**
- Modify: `modules/audio/HISTORY.md` (new subsection under the latest dated section)
- Modify: `modules/audio/docs/specs/2026-10-02-transcription-cancel-restart-design.md` (corrections below)

- [ ] **Step 1: Correct the spec**

In the spec:
- Section 3 "Known limit": replace with "Diarization reports progress through pyannote's `hook` (`diarization._progress_hook`), so a cancel lands during diarization as well. The measured latency per stage is in `HISTORY.md`."
- Section 4, `stalled && !restartable` row: replace "For a live meeting, one more line" with "The line also says a live recording may have no file to upload again: `meetings.source` does not tell a live meeting from an upload, so the sentence is shown to both."
- Section 2, restart: add "Requires the meeting `analyzing`: a worker that died between `mark_complete` and `mark_published` leaves a stale `running` job on a delivered meeting."
- Section 3, the commit fence: add "Every path that locks both rows takes the meeting first, then the job."

- [ ] **Step 2: Real run on an isolated local stack**

Follow `autune-isolated-local-run` (own API port 8010, Redis db 5, web 3010, fake B/C/D, small Whisper model). Then:

1. Upload a two-minute Korean test recording. While the stage reads 음성 인식, `kill -9` the Celery worker. Start it again. Watch `GET /api/audio/meetings/{id}` until `stalled: true` (about two minutes), press 다시 시작 on S12, and confirm the meeting reaches `complete` and the temp directory is empty.
2. Upload again; during recognition press 처리 취소 → 취소하기. Record the seconds until the worker logs `audio_process_stopped`, confirm the temp directory is empty, and confirm the worker log shows no `autune.transcript.ready` publish for that meeting.
3. Repeat 2 with the cancel pressed during 화자 분리, and record the latency.

- [ ] **Step 3: Record in HISTORY.md**

Add a subsection: what was built (one paragraph), the settings (`heartbeat_interval_s=30`, `stall_after_s=120`, provisional, why four missed heartbeats), and the measured cancel latency for recognition and diarization from Step 2, with the recording length and model used.

- [ ] **Step 4: Full checks and commit**

Run: `uv run pytest modules/audio/tests -q && uv run ruff check modules/audio && uv run mypy modules/audio/src && uv run lint-imports`
Expected: all pass.

```bash
git add modules/audio/HISTORY.md modules/audio/docs/specs/2026-10-02-transcription-cancel-restart-design.md
git commit -m "docs(audio): cancel and restart measured on a real run"
```

- [ ] **Step 5: Pull request**

Rebase on current `origin/main`, run the full audio suite and the web checks again, push, and open a PR to `main` titled "feat(audio): cancel a transcription, and restart one whose worker died". `docs/engineering/environments.md` changed, so request all four teammates as reviewers (one REST call per login) and @-mention them in a comment.
