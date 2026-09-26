"""Scheduling by name and by stamp: the rule, and what it refuses.

These run against throwaway apps, so they state what the rule *is*. That the
five modules' real tasks obey it is a different question, asked in
`test_celery_app.py` and in `apps/worker/tests/test_registration.py` -- a rule
verified only against its own fixture is the shape of test that passes while
the thing it describes is broken.

``app.task(shared=False)``, never ``celery.shared_task``: a shared task
registers into every app finalized afterwards -- including the real ones this
file's neighbours build -- and a fixture leaking into `beat_schedule`'s output
is hard to explain and harder to find. ``shared=False`` is not the default even
on ``app.task``, which is how it was found: one test's task failed another
test's app.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from celery import Celery
from celery.schedules import crontab

from autune_core.periodic import (
    SCHEDULE_ATTR,
    beat_schedule,
    is_periodic_task_name,
    periodic,
    schedule_of,
)


def _app(*, empty: bool = True) -> Celery:
    """An app of our own, with a registry of our own.

    Emptied after finalizing, not merely fresh. ``celery.shared_task``
    registers itself into *every* app finalized after its module was imported,
    so a throwaway app built in a session that has also built a real one --
    ``test_celery_app.py`` is in this directory -- arrives holding the five
    modules' real tasks, including the one real periodic task. That is enough
    to fail an exact assertion here for a reason that has nothing to do with
    the rule, and to make a task defined below silently *not* replace the
    registered one of the same name.

    ``empty=False`` keeps Celery's own builtins, for the one test about them.
    """
    app = Celery("test-periodic", broker="memory://", set_as_current=False)
    app.conf.result_backend = "cache+memory://"
    app.finalize()
    for name in list(app.tasks):
        if empty or name.startswith("autune."):
            del app.tasks[name]
    return app


@pytest.mark.parametrize(
    ("name", "periodic_name"),
    [
        ("autune.audio.periodic.sweep_orphans", True),
        ("autune.intelligence.periodic.weekly_report", True),
        # A module's ordinary tasks, which is most of the registry.
        ("autune.audio.process_recording", False),
        ("autune.gap.on_transcript_ready", False),
        # Celery's own.
        ("celery.backend_cleanup", False),
        # Near misses. `periodic` has to be the third segment: a module called
        # `periodic` does not exist, and a task called `periodic` is not one.
        ("autune.audio.periodic", False),
        ("autune.periodic.sweep", False),
        ("autune.audio.periodic.", False),
        ("autune.audio.periodic.sweep.orphans", False),
        ("periodic.audio.periodic.sweep", False),
    ],
)
def test_the_rule_is_a_periodic_segment_in_the_middle(name: str, periodic_name: bool) -> None:
    assert is_periodic_task_name(name) is periodic_name


def test_a_periodic_name_stays_inside_its_module_route() -> None:
    """Why the segment goes there rather than on the end.

    ``autune.<module>.*`` is already routed, so a periodic task needs no new
    route and lands on its module's queue -- which for A is where the files it
    sweeps are.
    """
    app = _app()
    app.conf.task_routes = {"autune.audio.*": {"queue": "gpu"}}
    assert app.amqp.router.route({}, "autune.audio.periodic.sweep_orphans")["queue"].name == "gpu"


def test_the_stamp_is_readable_whichever_side_of_shared_task_it_goes() -> None:
    """The two orders are indistinguishable at a glance.

    Under the task decorator the stamp lands on the function, which Celery
    keeps as the task's ``run``; over it, on the task object. Reading only one
    of the two would make a swapped order a task that silently never runs.
    """
    app = _app()

    @app.task(shared=False, name="autune.audio.periodic.under")
    @periodic(timedelta(hours=1))
    def under() -> None: ...

    @periodic(timedelta(hours=2))
    @app.task(shared=False, name="autune.audio.periodic.over")
    def over() -> None: ...

    assert schedule_of(app.tasks["autune.audio.periodic.under"]) == timedelta(hours=1)
    assert schedule_of(app.tasks["autune.audio.periodic.over"]) == timedelta(hours=2)


def test_a_task_with_no_stamp_has_no_schedule() -> None:
    app = _app()

    @app.task(shared=False, name="autune.audio.process_recording")
    def process(job_id: str) -> None: ...

    assert schedule_of(app.tasks["autune.audio.process_recording"]) is None


def test_a_crontab_is_a_schedule_and_seconds_are_not() -> None:
    """Celery takes a number of seconds; this does not.

    ``3600`` in a diff says nothing about what was meant, and the mistake it
    invites -- minutes read as seconds -- is a job running sixty times too
    often against a database.
    """
    assert periodic(crontab(day_of_week="mon", hour=0, minute=0)) is not None
    with pytest.raises(TypeError, match="not int"):
        periodic(3600)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="not float"):
        periodic(3600.0)  # type: ignore[arg-type]


def test_monday_nine_in_seoul_is_midnight_utc() -> None:
    """The conversion nobody should do twice.

    The app runs on UTC (``enable_utc``) and the team reads KST, which is
    UTC+9 with no daylight saving. Module E's weekly report wants Monday 09:00
    KST (#227), and this is what that is.
    """
    monday_nine_kst = crontab(day_of_week="mon", hour=0, minute=0)
    assert monday_nine_kst.hour == {0}
    assert monday_nine_kst.day_of_week == {1}


def test_the_schedule_is_one_entry_per_periodic_task() -> None:
    app = _app()

    @app.task(shared=False, name="autune.audio.periodic.sweep_orphans")
    @periodic(timedelta(hours=1))
    def sweep() -> None: ...

    @app.task(shared=False, name="autune.intelligence.periodic.weekly_report")
    @periodic(crontab(day_of_week="mon", hour=0, minute=0))
    def weekly() -> None: ...

    schedule = beat_schedule(app)

    assert sorted(schedule) == [
        "autune.audio.periodic.sweep_orphans",
        "autune.intelligence.periodic.weekly_report",
    ]
    assert schedule["autune.audio.periodic.sweep_orphans"] == {
        "task": "autune.audio.periodic.sweep_orphans",
        "schedule": timedelta(hours=1),
    }
    # No arguments, anywhere: a periodic task has no caller to take them from.
    assert all(set(entry) == {"task", "schedule"} for entry in schedule.values())


def test_scheduling_needs_no_change_anywhere_else() -> None:
    """The reason this design was chosen over a schedule in ``apps/worker``.

    Scheduling is defining the task; unscheduling is deleting it. Nothing in
    ``apps/`` and nothing in a registration block is edited either way, which
    is what invariant 6 asks for and what ``events.py`` already does for
    events.
    """
    app = _app()

    @app.task(shared=False, name="autune.audio.periodic.sweep_orphans")
    @periodic(timedelta(hours=1))
    def sweep() -> None: ...

    assert list(beat_schedule(app)) == ["autune.audio.periodic.sweep_orphans"]

    @app.task(shared=False, name="autune.gap.periodic.recheck")
    @periodic(timedelta(days=1))
    def recheck() -> None: ...

    assert len(beat_schedule(app)) == 2

    del app.tasks["autune.gap.periodic.recheck"]

    assert list(beat_schedule(app)) == ["autune.audio.periodic.sweep_orphans"]


def test_celerys_own_tasks_are_not_ours_to_schedule() -> None:
    """``celery.backend_cleanup`` is in the same registry and beat installs it
    itself; picking it up here would schedule it twice."""
    app = _app(empty=False)
    assert "celery.backend_cleanup" in app.tasks
    assert beat_schedule(app) == {}


def test_a_periodic_name_without_a_schedule_refuses_to_pass() -> None:
    """A task named as periodic that beat would never run.

    Half of the rule, and the half a typo produces: ``@periodic`` left off, or
    applied to the wrong function. Nothing downstream can notice -- the entry
    is simply absent, exactly as if the task did not exist.
    """
    app = _app()

    @app.task(shared=False, name="autune.audio.periodic.sweep_orphans")
    def sweep() -> None: ...

    with pytest.raises(RuntimeError, match="declares no schedule"):
        beat_schedule(app)


def test_a_schedule_under_a_name_nobody_scans_refuses_to_pass() -> None:
    """The other half: the period is declared, the name hides it.

    ``@periodic`` on ``autune.audio.sweep_orphans`` looks finished in review and
    runs never.
    """
    app = _app()

    @app.task(shared=False, name="autune.audio.sweep_orphans")
    @periodic(timedelta(hours=1))
    def sweep() -> None: ...

    with pytest.raises(RuntimeError, match="not named"):
        beat_schedule(app)


def test_a_stamp_that_is_not_a_schedule_is_ignored_rather_than_handed_to_beat() -> None:
    """Defence against the attribute name being reused by accident.

    ``SCHEDULE_ATTR`` is ours, but the registry is not: anything can set an
    attribute on a task. A value beat cannot interpret would fail at the first
    tick, in a process nobody is watching, so it is not treated as a schedule
    here -- and then the missing-schedule half of the rule reports it.
    """
    app = _app()

    @app.task(shared=False, name="autune.audio.periodic.sweep_orphans")
    def sweep() -> None: ...

    task: Any = app.tasks["autune.audio.periodic.sweep_orphans"]
    setattr(task, SCHEDULE_ATTR, "every hour")

    assert schedule_of(task) is None
    with pytest.raises(RuntimeError, match="declares no schedule"):
        beat_schedule(app)
