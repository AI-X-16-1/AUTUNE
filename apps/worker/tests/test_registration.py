"""Every module's tasks must register, and land on the right queue."""

from __future__ import annotations

import pytest

from autune_contracts import EVENTS, MODULES, TERMINAL_EVENTS
from autune_core import consumer_task_suffix
from autune_worker import celery_app


@pytest.fixture(scope="module")
def task_names() -> set[str]:
    celery_app.loader.import_default_modules()
    return {name for name in celery_app.tasks if name.startswith("autune.")}


@pytest.mark.parametrize("module", MODULES)
def test_module_has_at_least_one_task(module: str, task_names: set[str]) -> None:
    assert any(name.startswith(f"autune.{module}.") for name in task_names), (
        f"{module} registered no tasks"
    )


def test_audio_runs_on_the_gpu_queue(task_names: set[str]) -> None:
    """STT belongs on the GPU worker; a short task there wastes it."""
    routes = celery_app.conf.task_routes
    assert routes["autune.audio.*"]["queue"] == "gpu"


def test_context_notify_does_not_wait_behind_cpu_heavy_work() -> None:
    """The Slack-only task overrides the module's ``cpu_heavy`` wildcard, the
    same way ``autune.intelligence.aggregate`` overrides its module's default --
    exact task names win over a glob in Celery's router regardless of dict
    order, so this checks the resolved route, not just the raw config."""
    route = celery_app.amqp.router.route({}, "autune.context.notify_context_events")
    assert route["queue"].name == "default"


def test_intelligence_consumes_all_three_upstream_modules(task_names: set[str]) -> None:
    for upstream in ("extraction", "gap", "context"):
        assert f"autune.intelligence.on_{upstream}_completed" in task_names


@pytest.mark.parametrize("event", [e for e in EVENTS if e not in TERMINAL_EVENTS])
def test_every_event_has_a_subscriber(event: str, task_names: set[str]) -> None:
    """Producer side: the name a producer publishes resolves to real tasks.

    `autune_core.publish` derives subscribers from the event name, so an event
    whose consumers were never written -- or were written under a name that does
    not follow the rule -- publishes into nothing and logs a warning. This is the
    test that makes that loud.
    """
    suffix = consumer_task_suffix(event)
    assert any(name.endswith(f".{suffix}") for name in task_names), (
        f"nothing consumes {event}; expected a task named autune.<module>.{suffix}"
    )


def test_every_consumer_task_names_a_real_event(task_names: set[str]) -> None:
    """Consumer side, and the half that catches a typo.

    A task called `autune.gap.on_transcipt_ready` registers happily, consumes
    nothing, and nothing complains -- the producer's event simply has one fewer
    subscriber than its author thought. Reversing the rule turns that into a
    failing test.
    """
    expected = {consumer_task_suffix(event) for event in EVENTS}
    consumers = {n for n in task_names if n.rsplit(".", 1)[-1].startswith("on_")}
    for name in consumers:
        assert name.rsplit(".", 1)[-1] in expected, (
            f"{name} subscribes to an event that does not exist; expected one of {sorted(expected)}"
        )


def test_the_rule_holds_for_every_event_and_consumer(task_names: set[str]) -> None:
    """The two halves above, stated as the one property they share.

    Every `on_*` task is reachable from exactly one declared event, and every
    non-terminal event reaches at least one task. If that stops being true, the
    mechanical rule has drifted from the names in use and `publish` is quietly
    delivering to fewer modules than the diagram says.
    """
    by_event = {event: consumer_task_suffix(event) for event in EVENTS}
    reachable = {n for n in task_names if n.rsplit(".", 1)[-1] in set(by_event.values())}
    on_tasks = {n for n in task_names if n.rsplit(".", 1)[-1].startswith("on_")}
    assert reachable == on_tasks
