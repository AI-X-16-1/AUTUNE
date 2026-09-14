"""Publishing by name: the rule, and what happens when nobody is listening.

These run against a stand-in registry so they state what the rule *is*. That it
matches the tasks five modules actually registered is a different question, and
`apps/worker/tests/test_registration.py` is where it is asked — a rule verified
only against its own fixture is the shape of test that passes while the thing it
describes is broken.
"""

from __future__ import annotations

from typing import Any

import pytest

from autune_contracts import EVENTS, EXTRACTION_COMPLETED, TRANSCRIPT_READY
from autune_core import consumer_task_suffix, events, publish, subscribers


class FakeApp:
    """Just enough Celery: a task registry and a record of what was sent."""

    def __init__(self, *task_names: str) -> None:
        self.tasks = dict.fromkeys(task_names)
        self.sent: list[tuple[str, list[Any]]] = []

    def send_task(self, name: str, args: list[Any]) -> None:
        self.sent.append((name, args))


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FakeApp:
    fake = FakeApp(
        "autune.extraction.on_transcript_ready",
        "autune.gap.on_transcript_ready",
        "autune.context.on_transcript_ready",
        "autune.context.on_extraction_completed",
        "autune.intelligence.on_extraction_completed",
        "autune.audio.process_recording",
        "celery.backend_cleanup",
    )
    monkeypatch.setattr(events, "current_app", fake)
    return fake


@pytest.mark.parametrize(
    ("event", "suffix"),
    [
        ("autune.transcript.ready", "on_transcript_ready"),
        ("autune.extraction.completed", "on_extraction_completed"),
        ("autune.intelligence.completed", "on_intelligence_completed"),
    ],
)
def test_the_rule_is_segment_joining(event: str, suffix: str) -> None:
    """Not "the producer's name, then the noun".

    `autune.transcript.ready` is produced by `audio` and does not contain it, so
    a rule written by meaning breaks on the first event we have.
    """
    assert consumer_task_suffix(event) == suffix


def test_every_declared_event_follows_the_rule() -> None:
    for event in EVENTS:
        assert consumer_task_suffix(event).startswith("on_")
        assert "." not in consumer_task_suffix(event)


def test_one_event_reaches_every_subscriber(app: FakeApp) -> None:
    """Three modules consume a transcript. The producer names none of them."""
    sent_to = publish(TRANSCRIPT_READY, {"meeting_id": "m1"})

    assert sent_to == [
        "autune.context.on_transcript_ready",
        "autune.extraction.on_transcript_ready",
        "autune.gap.on_transcript_ready",
    ]
    assert [name for name, _ in app.sent] == sent_to
    assert all(args == [{"meeting_id": "m1"}] for _, args in app.sent)


def test_a_second_consumer_needs_no_change_to_the_producer(app: FakeApp) -> None:
    """The reason this design was chosen over naming consumers directly.

    Subscribing is defining the task. Nothing in the producer, and nothing in a
    registration block, has to be edited — which matters because the producer
    belongs to somebody else (invariant 10).
    """
    assert len(subscribers(EXTRACTION_COMPLETED)) == 2

    app.tasks["autune.gap.on_extraction_completed"] = None

    assert len(subscribers(EXTRACTION_COMPLETED)) == 3
    assert "autune.gap.on_extraction_completed" in publish(EXTRACTION_COMPLETED, {})


def test_unrelated_tasks_are_not_subscribers(app: FakeApp) -> None:
    """A producer's own task and Celery's internals are in the same registry."""
    for name, _ in app.sent:
        assert name != "autune.audio.process_recording"
    assert publish(TRANSCRIPT_READY, {}) == subscribers(TRANSCRIPT_READY)
    assert "celery.backend_cleanup" not in subscribers(TRANSCRIPT_READY)


def test_nobody_listening_is_a_warning_not_a_failure(app: FakeApp) -> None:
    """C not being deployed yet must not fail B.

    "A failed module does not fail the others" — async-pipeline.md. The last
    event in the pipeline has no subscribers by design, and it publishes fine.
    """
    assert publish("autune.intelligence.completed", {"meeting_id": "m1"}) == []
    assert app.sent == []


def test_an_undeclared_event_raises_rather_than_going_nowhere(app: FakeApp) -> None:
    """The failure this rule is most exposed to is a typo in the event name.

    It resolves to a suffix nobody registered, sends to nothing, and says
    nothing — a meeting that is simply never analysed. Checked against the
    declared constants so it fails at the call site instead.
    """
    with pytest.raises(ValueError, match="not a declared event"):
        publish("autune.transcript.redy", {})
    assert app.sent == []
