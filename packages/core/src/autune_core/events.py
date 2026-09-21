"""Publish an event without knowing who receives it.

`module-boundaries.md` and `async-pipeline.md` described two different ways of
doing this and neither existed in code, so nobody could publish anything and all
five modules stopped at the same line (#101). This is the agreed one.

**A producer names the event; it never names a consumer.** Writing
``send_task("autune.extraction.on_transcript_ready", ...)`` inside module A puts
B's task name in A's source. import-linter does not see it -- it is a string --
so the coupling arrives silently, and `autune.extraction.completed` already has
two consumers, which means B's source would carry D's and E's names. Adding a
third consumer would then mean editing the *producer*, which belongs to somebody
else (invariant 10). A rule nobody can follow is a rule that gets broken.

**Subscribers are derived from the name, not from a list.** There is no registry
to append to (invariant 6), and no file where a producer records its consumers.
A module subscribes by defining the task and unsubscribes by deleting it.

The rule is **mechanical**, deliberately: drop the ``autune.`` prefix, join what
is left with underscores, prefix ``on_``.

    autune.transcript.ready        ->  on_transcript_ready
    autune.extraction.completed    ->  on_extraction_completed

Every task registered as ``autune.<consumer>.<that suffix>`` receives the event.
Written the other way round -- "the producer's name, then the noun" -- the rule
breaks on the first event we have: `autune.transcript.ready` is produced by
`audio`, and `audio` is nowhere in it. Describing it by meaning would invite the
next person to name A's next event `autune.audio.transcript_ready`, and then
neither name follows the rule. Joining the segments works on all five.

Nothing here knows a module name. The names come from the Celery registry, which
is populated from ``autune_contracts.MODULES`` by ``apps/worker``.
"""

from __future__ import annotations

from typing import Any

from celery import current_app

from autune_contracts import EVENTS, TERMINAL_EVENTS

from .logging import get_logger

log = get_logger(__name__)

EVENT_PREFIX = "autune."


def consumer_task_suffix(event: str) -> str:
    """``autune.transcript.ready`` -> ``on_transcript_ready``.

    The whole naming rule, in one place, so a test can assert it against the
    tasks that are actually registered rather than against a copy of it.
    """
    return "on_" + event.removeprefix(EVENT_PREFIX).replace(".", "_")


def subscribers(event: str) -> list[str]:
    """Registered task names subscribed to ``event``, sorted.

    Sorted so the order does not depend on how Celery happened to import the
    modules -- a log line and a test both read this.

    A producer that also defines the consuming task would receive its own event.
    That is not filtered out, because the event name does not say who produced
    it -- which is the point of the rule -- and there is no other way to know.
    No module does this today; if one ever wants to, it should call the function
    rather than go through the broker.
    """
    suffix = consumer_task_suffix(event)
    return sorted(
        name
        for name in current_app.tasks
        if name.startswith(EVENT_PREFIX)
        and name.count(".") == 2
        and name.rsplit(".", 1)[-1] == suffix
    )


def _any_module_tasks() -> bool:
    """Whether this process has any of our tasks registered at all.

    The question is not "does this event have consumers" but "was this process
    built to have any": a worker that never imported `autune_<module>.tasks`
    answers no to every event, and that is not the same as an event nobody
    subscribes to.
    """
    return any(name.startswith(EVENT_PREFIX) for name in current_app.tasks)


def publish(event: str, payload: dict[str, Any]) -> list[str]:
    """Send ``payload`` to every task subscribed to ``event``. Returns their names.

    ``event`` must be one of the constants in ``autune_contracts.events``. A
    string typed by hand is the failure this rule is most exposed to: a
    mis-typed name resolves to a suffix nobody registered, nothing is sent, and
    nothing says so. Checking it here turns that into an exception at the call
    site instead of a meeting that silently never gets analysed.

    **No subscribers is a warning, not an error.** C not being deployed yet must
    not fail B -- "a failed module does not fail the others", async-pipeline.md.
    The terminal event of the pipeline has no subscribers by design.

    The fan-out is not atomic: with two subscribers, the worker can die after the
    first send. Consuming tasks are required to be idempotent, which is what
    makes that survivable and is already a requirement of theirs.

    The payload is never logged. Every event payload is a contract type built
    from meeting content, and a log line is a store like any other
    (docs/architecture/privacy.md).
    """
    if event not in EVENTS:
        # ValueError rather than an AutuneError: this is a name typed wrong in
        # our own source, not input to validate, and nothing should turn it into
        # a 422. settings.py and the contract models raise the same way for the
        # same reason.
        raise ValueError(
            f"{event!r} is not a declared event; import the constant from "
            f"autune_contracts.events rather than typing the name. "
            f"Declared: {', '.join(EVENTS)}"
        )

    targets = subscribers(event)
    if not targets:
        # Three different things looked identical here, and the harmless one
        # happens on every meeting -- which is how the other two got buried.
        #
        # `event_name`, not `event`: structlog takes the first positional as the
        # field it calls `event`, and passing both is a TypeError.
        if not _any_module_tasks():
            # Nothing of ours is registered at all, so this process never
            # imported the module tasks. Every event published from here reaches
            # nobody and the meeting is not analysed. Raising rather than
            # logging for the same reason the undeclared-event check above
            # raises: it is a deployment mistake, it will not fix itself, and a
            # log line is what it had before this was reported (#170).
            raise RuntimeError(
                f"{event!r} reached no task because no autune task is registered in "
                "this process. publish() needs the module tasks imported -- run it "
                "from apps/worker, or import autune_<module>.tasks first."
            )
        if event in TERMINAL_EVENTS:
            # The pipeline ended. Recorded, not warned about.
            log.info("event_terminal", event_name=event)
            return []
        log.warning("event_no_subscribers", event_name=event)
        return []

    for name in targets:
        current_app.send_task(name, args=[payload])

    log.info("event_published", event_name=event, subscribers=len(targets))
    return targets
