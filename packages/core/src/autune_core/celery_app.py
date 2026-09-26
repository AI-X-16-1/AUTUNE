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

The beat schedule is assembled here for the same reason: it is derived from the
task registry (``periodic.py``), and this is the file that already owns what the
worker knows and the client does not.
"""

from __future__ import annotations

from celery import Celery

from autune_contracts import MODULES

from .periodic import beat_schedule
from .settings import get_settings

# A long task on `default` blocks Slack notifications; a short one on `gpu`
# wastes an expensive worker.
TASK_ROUTES: dict[str, dict[str, str]] = {
    "autune.audio.*": {"queue": "gpu"},
    "autune.extraction.*": {"queue": "cpu_heavy"},
    "autune.gap.*": {"queue": "cpu_heavy"},
    "autune.context.*": {"queue": "cpu_heavy"},
    # Only the Slack calls -- no embedder/reranker/NLI model work -- so it does
    # not belong on the worker reserved for that.
    "autune.context.notify_context_events": {"queue": "default"},
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

    **The worker app also carries the beat schedule**, derived from the tasks
    the modules registered (``periodic.py``). A client gets none: it imports no
    task module, so it has nothing to derive one from, and beat is a separate
    process built from the worker app anyway. See
    docs/architecture/async-pipeline.md for the command.
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
    if include_tasks:
        # `include=[...]` is imported at finalization, not at construction, so
        # the registry is still empty on the line above and a scan here would
        # find no periodic task at all. An empty `beat_schedule` raises nothing
        # and reads exactly like "no module asked for one", so the failure would
        # be a job that never runs and nothing saying so -- the whole reason
        # this mechanism exists. Pinned by
        # test_the_task_modules_are_imported_before_the_registry_is_scanned.
        app.loader.import_default_modules()
        app.conf.beat_schedule = beat_schedule(app)
    app.set_default()
    return app
