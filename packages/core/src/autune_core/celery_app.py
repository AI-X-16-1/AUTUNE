"""The Celery app, built here so every process gets the same one.

``publish`` in ``events.py`` and each module's enqueue send through
``celery.current_app``. Inside the worker that resolved to ``apps/worker``'s
app. Inside the API process there was no app at all, so ``current_app`` fell
back to Celery's built-in default: broker ``amqp://guest@localhost//``, no
``task_routes``. An upload returned 202, the message left for a broker that
does not exist, and the meeting stayed ``analyzing`` forever (#258).

The alternative -- the API importing the worker's app -- would drag every
module's ``tasks.py`` into a process that never runs a task, and with it the
decoder, the diarizer and Whisper. So the factory takes one flag: a **worker**
includes the task modules, a **client** does not and sends by name.

Routes live here and nowhere else. A module that copies them gets one wrong
eventually, and then only its tasks quietly land on the wrong queue.
"""

from __future__ import annotations

from celery import Celery

from autune_contracts import MODULES

from .settings import get_settings

# A long task on `default` blocks Slack notifications; a short one on `gpu`
# wastes an expensive worker.
TASK_ROUTES: dict[str, dict[str, str]] = {
    "autune.audio.*": {"queue": "gpu"},
    "autune.extraction.*": {"queue": "cpu_heavy"},
    "autune.gap.*": {"queue": "cpu_heavy"},
    "autune.context.*": {"queue": "cpu_heavy"},
    "autune.intelligence.aggregate": {"queue": "cpu_heavy"},
    "autune.intelligence.*": {"queue": "default"},
}


def make_celery_app(*, include_tasks: bool) -> Celery:
    """Build the app and make it the one ``current_app`` returns -- everywhere.

    ``include_tasks=True`` is the worker: it imports ``autune_<module>.tasks``
    for every module in ``MODULES`` so the registry is full and ``subscribers``
    can derive consumers from event names. ``include_tasks=False`` is a client
    such as ``apps/api``: it sends by task name, ``TASK_ROUTES`` still applies,
    and nothing heavy is imported. A client's registry is empty, so ``publish``
    from a client finds no subscribers; that is a known limit, not a bug in the
    caller, and the demo path does not need it.

    ``set_default`` matters as much as ``set_as_current``. ``current_app`` is
    thread-local, and a thread that never set one -- every FastAPI threadpool
    thread -- gets the *default* app. Without this line the import-time check
    sees Redis and the request handler sends to ``amqp://guest@localhost//``.
    """
    settings = get_settings()
    app = Celery(
        "autune",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=[f"autune_{name}.tasks" for name in MODULES] if include_tasks else [],
    )
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_routes=TASK_ROUTES,
    )
    app.set_default()
    return app
