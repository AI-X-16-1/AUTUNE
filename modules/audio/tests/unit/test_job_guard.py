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
