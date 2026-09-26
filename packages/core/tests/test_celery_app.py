"""One Celery app, built in one place, current in every process.

``publish`` and every module's enqueue go through ``celery.current_app``. In the
worker that was ``apps/worker``'s app; in the API process it was Celery's
built-in default -- broker ``amqp://guest@localhost//``, no ``task_routes`` --
so an upload returned 202 and the message went nowhere (#258). These tests pin
the properties that make the factory the fix rather than a second copy.
"""

from __future__ import annotations

import threading

import pytest
from celery import Celery, current_app
from celery.loaders.app import AppLoader

from autune_contracts import MODULES
from autune_core import celery_app, get_settings, is_periodic_task_name
from autune_core.celery_app import TASK_ROUTES, make_celery_app


def test_broker_and_backend_come_from_settings() -> None:
    app = make_celery_app(include_tasks=False)
    assert app.conf.broker_url == get_settings().redis_url
    assert app.conf.result_backend == get_settings().redis_url


def test_the_new_app_is_current_in_the_calling_thread() -> None:
    app = make_celery_app(include_tasks=False)
    assert current_app._get_current_object() is app


def test_the_new_app_is_current_in_other_threads_too() -> None:
    """FastAPI runs sync routes on a threadpool.

    ``current_app`` is thread-local and falls back to Celery's *default* app in
    a thread that never set one. Unless the factory also makes ours the default,
    a route handler sends to ``amqp://guest@localhost//`` while the import-time
    check in ``main.py`` happily sees Redis.
    """
    app = make_celery_app(include_tasks=False)
    seen: list[Celery] = []
    thread = threading.Thread(target=lambda: seen.append(current_app._get_current_object()))
    thread.start()
    thread.join()
    assert seen == [app]


def test_routes_are_declared_once_and_every_module_is_routed() -> None:
    app = make_celery_app(include_tasks=False)
    assert app.conf.task_routes is TASK_ROUTES
    for module in MODULES:
        assert any(pattern.startswith(f"autune.{module}.") for pattern in TASK_ROUTES), (
            f"{module} has no route; its tasks would land on the default queue"
        )
    assert TASK_ROUTES["autune.audio.*"]["queue"] == "gpu"


def test_a_client_app_does_not_import_task_modules() -> None:
    """The API process sends by name; it must not load five inference stacks."""
    app = make_celery_app(include_tasks=False)
    assert list(app.conf.include) == []


def test_a_worker_app_includes_every_module_task_file() -> None:
    app = make_celery_app(include_tasks=True)
    assert list(app.conf.include) == [f"autune_{name}.tasks" for name in MODULES]


def test_a_worker_app_carries_the_periodic_tasks_the_modules_declared() -> None:
    """Beat is handed a real schedule, not a dict-building function that works.

    Built through the factory with the module task files actually imported, and
    asserted against the one periodic task that exists today (module A's orphan
    sweep). If this is empty, beat runs nothing and says nothing -- see the
    ordering test below for the line that keeps it from being empty.

    The interval is A's to choose and is asserted in A's own tests; what belongs
    here is that the entry arrived at all, and that it points at the task.
    """
    app = make_celery_app(include_tasks=True)
    schedule = app.conf.beat_schedule

    assert "autune.audio.periodic.sweep_orphans" in schedule, (
        f"beat would run nothing; schedule is {schedule!r}"
    )
    entry = schedule["autune.audio.periodic.sweep_orphans"]
    assert entry["task"] == "autune.audio.periodic.sweep_orphans"
    assert entry["schedule"] is not None
    for name in schedule:
        assert is_periodic_task_name(name)


def test_the_task_modules_are_imported_before_the_registry_is_scanned() -> None:
    """The one line in the factory that must not be removed.

    ``include=[...]`` goes to the ``Celery()`` constructor and is imported at
    finalization, so the registry is empty while the factory is still running.
    Scanning it then yields ``{}`` -- which raises nothing, looks exactly like
    "no module asked for a periodic task", and is the silent failure the whole
    mechanism exists to prevent.

    The order is asserted rather than the outcome, so this stays true in a
    process where something else has already imported the module tasks: with
    ``shared_task``, every app finalized after that import gets them anyway, and
    then the test above would pass with the import line deleted.
    """
    order: list[str] = []

    def note_import(self: AppLoader) -> list[object]:
        order.append("import")
        return []

    def note_scan(app: Celery) -> dict[str, dict[str, object]]:
        order.append("scan")
        return {}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(AppLoader, "import_default_modules", note_import)
        patch.setattr(celery_app, "beat_schedule", note_scan)
        make_celery_app(include_tasks=True)

    assert order == ["import", "scan"]


def test_a_client_app_gets_no_beat_schedule() -> None:
    """A client imports no task module -- it has nothing to derive one from, and
    deriving one would mean loading five inference stacks into the API (#258).
    Beat is a separate process, built from the worker app."""
    app = make_celery_app(include_tasks=False)
    assert app.conf.beat_schedule == {}


def test_a_task_sent_by_name_is_routed_without_being_registered() -> None:
    """The client never registers ``autune.audio.process_recording``; the
    route still has to send it to ``gpu``, or the worker listening there
    never sees it."""
    app = make_celery_app(include_tasks=False)
    options = app.amqp.router.route({}, "autune.audio.process_recording")
    assert options["queue"].name == "gpu"
