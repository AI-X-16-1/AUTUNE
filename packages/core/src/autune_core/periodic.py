"""Run a task on a schedule, without a schedule to append to.

`apps/` is assembly only, and a module may never hand-append itself to a
registration block (invariant 6). Routers and Celery tasks are already
discovered by iterating the module list; a periodic task had no such rule. The
only place a ``beat_schedule`` could live was ``apps/worker``, shared code that
five people would take turns editing -- exactly the shape the invariant forbids
-- so nobody could ship a scheduled job at all (#207).

**The schedule is derived from the registry, not from a list.** This is the
property ``events.py`` already runs on, applied to time instead of to events:
a module schedules a task by defining it and unschedules it by deleting it.
There is nothing to append, and ``apps/worker`` gains no line when module E's
weekly report arrives (#227).

It takes two halves, because a task name can say *that* something is periodic
but not *how often*:

**The name.** ``autune.<module>.periodic.<name>`` -- how discovery finds it
without knowing a module name. It stays inside ``autune.<module>.*``, so
`TASK_ROUTES` sends it to the module's normal queue and a periodic task needs
no new route: ``autune.audio.periodic.sweep_orphans`` lands on ``gpu`` with A's
other tasks, which is where A's files are.

**The decorator.** ``@periodic(timedelta(hours=1))``, next to
``celery.shared_task``, stamping the period onto the task that
``make_celery_app`` reads when it builds the schedule.

Two rules for whoever writes one:

- **It must be safe to run twice and safe to overlap.** Beat restarts, and the
  previous run may not have finished when the next is due. Celery serialises
  neither, and nothing here does it for you: that is the module author's
  responsibility, the way idempotence already is for an event consumer.
- **Every schedule is declared in UTC**, because the app is (``enable_utc``).
  KST is UTC+9, so ``crontab(hour=0, minute=0)`` fires at 09:00 KST, and Monday
  09:00 KST is ``crontab(day_of_week="mon", hour=0, minute=0)``.

Nothing here knows a module name, and nothing here imports a module.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any, TypeVar

from celery import Celery
from celery.schedules import BaseSchedule

TASK_PREFIX = "autune."
PERIODIC_SEGMENT = "periodic"

# Not a name Celery knows; `make_celery_app` is the only reader.
SCHEDULE_ATTR = "autune_periodic_schedule"

# `timedelta` or one of Celery's schedule objects (`crontab`, `solar`). A bare
# number of seconds is deliberately not part of this -- see `periodic`.
# A `type` statement, not a bare assignment: celery ships no type information,
# so `BaseSchedule` is `Any` here and mypy reads the assignment as a variable
# rather than an alias.
type Schedule = timedelta | BaseSchedule

F = TypeVar("F", bound=Callable[..., Any])


def is_periodic_task_name(name: str) -> bool:
    """``autune.<module>.periodic.<name>``, and nothing else.

    The whole naming rule, in one place, so a test can assert it against the
    tasks that are actually registered rather than against a copy of it.
    """
    parts = name.split(".")
    return len(parts) == 4 and parts[0] == "autune" and parts[2] == PERIODIC_SEGMENT and all(parts)


def periodic(schedule: Schedule) -> Callable[[F], F]:
    """Declare how often the task below it runs.

    Applied next to ``celery.shared_task``; either side of it works::

        @shared_task(name="autune.audio.periodic.sweep_orphans")
        @periodic(timedelta(hours=1))
        def sweep_orphans() -> None: ...

    Inside ``shared_task`` the stamp lands on the function, which Celery keeps
    as the task's ``run``; outside it, on the task object itself.
    ``schedule_of`` looks in both places, because the two orders are
    indistinguishable at a glance and a stamp read from only one of them is a
    task that silently never runs.

    A bare number of seconds is refused although Celery accepts one: ``3600``
    in a diff says nothing about what was meant, and ``timedelta(hours=1)``
    costs nothing. Raising rather than coercing for the reason ``publish``
    raises on an undeclared event -- it is our own source that is wrong.
    """
    if not isinstance(schedule, timedelta | BaseSchedule):
        raise TypeError(
            f"a schedule must be a timedelta or a celery schedule such as crontab, "
            f"not {type(schedule).__name__}; seconds as a number say nothing about "
            f"what was intended"
        )

    def stamp(task: F) -> F:
        setattr(task, SCHEDULE_ATTR, schedule)
        return task

    return stamp


def schedule_of(task: Any) -> Schedule | None:
    """The period stamped on ``task``, or ``None`` if it is not periodic.

    Both decorator orders, read in one place: Celery builds a task class whose
    ``run`` is the decorated function, and function attributes do not become
    class attributes, so a stamp applied under ``shared_task`` is only ever
    found on ``run``.
    """
    found = getattr(task, SCHEDULE_ATTR, None)
    if found is None:
        found = getattr(getattr(task, "run", None), SCHEDULE_ATTR, None)
    return found if isinstance(found, timedelta | BaseSchedule) else None


def beat_schedule(app: Celery) -> dict[str, dict[str, Any]]:
    """Every periodic task registered in ``app``, as beat's schedule.

    **The app has to have imported the task modules already.** ``include=[...]``
    is imported at finalization, not at construction, so scanning a freshly
    built app finds nothing -- and an empty schedule is not an error, it looks
    exactly like no module having asked for one. ``make_celery_app`` forces the
    import before it calls this; nothing here can tell the difference.

    Entries are keyed by task name: it is unique, it is what beat logs, and it
    is what beat's persistent file remembers, so renaming a task creates a new
    entry rather than quietly reusing the old one's last-run time.

    No entry carries arguments. A periodic task has no caller to take them
    from, so it takes none and reads whatever it needs inside the run; that
    keeps settings out of the schedule, where nobody would look for them.

    **Both halves of the rule are checked, and a mismatch raises.** A periodic
    name with no schedule, or a schedule under a name discovery does not look
    at (``@periodic`` on a task called ``autune.audio.sweep``), is a task that
    never runs, and a schedule missing an entry is indistinguishable from a
    schedule nobody asked for. Same choice ``publish`` makes for a mistyped
    event name: our own source is wrong, it will not fix itself, and the worker
    refusing to start says so once instead of a job never running for a month.
    """
    entries: dict[str, dict[str, Any]] = {}
    for name, task in app.tasks.items():
        if not name.startswith(TASK_PREFIX):
            # Celery's own tasks -- `celery.backend_cleanup` and friends. Beat
            # installs those itself; they are not ours to schedule.
            continue
        schedule = schedule_of(task)
        if is_periodic_task_name(name):
            if schedule is None:
                raise RuntimeError(
                    f"{name} is named as a periodic task but declares no schedule; "
                    f"add @periodic(...) from autune_core next to its shared_task, "
                    f"or rename it -- as it stands beat will never run it"
                )
            entries[name] = {"task": name, "schedule": schedule}
        elif schedule is not None:
            raise RuntimeError(
                f"{name} declares a schedule but is not named "
                f"autune.<module>.periodic.<name>, so nothing will find it; "
                f"rename the task or drop the @periodic decorator"
            )
    return entries
