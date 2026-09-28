"""A's periodic task: registered under a name discovery finds, and hourly.

The sweep itself is tested against a real database
(`tests/integration/test_tasks.py`); what is asked here is the part that makes
it run at all -- the name, the period, the queue, and that a periodic run spares
no file. Getting any of those wrong is a task that exists and never fires.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from autune_audio import tasks
from autune_core.celery_app import TASK_ROUTES, make_celery_app
from autune_core.periodic import is_periodic_task_name, schedule_of

TASK_NAME = "autune.audio.periodic.sweep_orphans"


def test_the_task_is_named_so_the_schedule_can_find_it() -> None:
    """``autune.audio.periodic.<name>``. A task named anything else is a task
    ``beat_schedule`` never looks at, and nothing downstream notices."""
    assert tasks.sweep_orphans.name == TASK_NAME
    assert is_periodic_task_name(TASK_NAME)


def test_the_sweep_runs_hourly() -> None:
    """Hourly is chosen against ``orphan_after_hours`` (6h), not by feel: a
    shorter interval cannot collect a ``queued`` or ``running`` job's file any
    sooner and scans the whole upload directory to find that out."""
    assert schedule_of(tasks.sweep_orphans) == timedelta(hours=1)


def test_the_sweep_lands_on_the_queue_the_files_are_on() -> None:
    """``autune.audio.*`` already covers it, so the periodic name needs no new
    route -- and it must not get one: the sweep deletes files in
    ``AUTUNE_AUDIO_TEMP_DIR``, so it has to run in the process that can see
    them, which is the worker A's other tasks run on."""
    app = make_celery_app(include_tasks=False)
    assert app.amqp.router.route({}, TASK_NAME)["queue"].name == "gpu"
    assert TASK_ROUTES["autune.audio.*"]["queue"] == "gpu"


def test_a_periodic_run_spares_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """``keep`` exists for ``process_recording`` protecting its own upload.

    A run on a schedule owns no job, so passing one would spare a file that
    nobody is coming for -- and passing the wrong one would delete a file a
    live task is about to adopt.
    """
    calls: list[dict[str, Any]] = []
    sentinel = object()

    class Scope:
        def __enter__(self) -> object:
            return sentinel

        def __exit__(self, *exc: object) -> None:
            return None

    def fake_sweep(session: object, *, settings: object, keep: str | None = None) -> list[str]:
        calls.append({"session": session, "settings": settings, "keep": keep})
        return ["job-1", "job-2"]

    monkeypatch.setattr(tasks, "session_scope", Scope)
    monkeypatch.setattr(tasks.service, "sweep_orphans", fake_sweep)

    assert tasks.sweep_orphans() is None

    assert len(calls) == 1
    assert calls[0]["session"] is sentinel
    assert calls[0]["keep"] is None


def test_the_sweep_logs_a_count_and_no_filenames(monkeypatch: pytest.MonkeyPatch) -> None:
    """The filenames are job ids and the log is a store like any other
    (privacy.md section 1), so the line says how many, not which."""
    from structlog.testing import capture_logs

    class Scope:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(tasks, "session_scope", Scope)
    monkeypatch.setattr(
        tasks.service, "sweep_orphans", lambda session, **kw: ["job-1", "job-2", "job-3"]
    )

    with capture_logs() as logs:
        tasks.sweep_orphans()

    assert [(entry["event"], entry["swept"]) for entry in logs] == [
        ("audio_orphan_sweep_finished", 3)
    ]
    assert all("job-1" not in str(entry) for entry in logs)
