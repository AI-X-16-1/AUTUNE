"""The one class that changes if live transcription moves to a worker.

What it promises now: calls are serialised (one lock per process) and the
event loop is not blocked while one runs.
"""

from __future__ import annotations

import asyncio
import threading
import time

import numpy as np
import pytest

from autune_audio import pipeline
from autune_audio.live import backends
from autune_audio.live.transcriber import Transcriber
from autune_audio.schemas import SAMPLE_RATE, Transcription, Waveform


def waveform(seconds: float = 0.5) -> Waveform:
    return Waveform(samples=np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32))


def empty() -> Transcription:
    return Transcription(segments=(), language="ko", language_probability=1.0, duration=0.5)


@pytest.mark.asyncio
async def test_two_calls_do_not_overlap() -> None:
    """Verify that concurrent calls wait for each other: peak occupancy is 1."""
    inside = 0
    peak = 0
    lock = threading.Lock()
    events: list[tuple[float, str]] = []

    def slow(_: Waveform) -> Transcription:
        nonlocal inside, peak
        with lock:
            inside += 1
            peak = max(peak, inside)
            events.append((time.monotonic(), f"enter:{inside}"))
        time.sleep(0.08)
        with lock:
            inside -= 1
            events.append((time.monotonic(), f"exit:{inside}"))
        return empty()

    transcriber = Transcriber(transcribe=slow, warm_up=lambda: None)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        for _ in range(10):
            await asyncio.sleep(0.01)
            ticks += 1

    await asyncio.gather(
        transcriber.run(waveform()),
        transcriber.run(waveform()),
        ticker(),
    )

    assert peak == 1, f"peak occupancy should be 1, got {peak}"
    # Verify the second call did not start until the first exited
    assert len(events) == 4
    assert events[0][1] == "enter:1"
    assert events[1][1] == "exit:0"
    assert events[2][1] == "enter:1"
    assert events[3][1] == "exit:0"
    # Verify we got several ticks while transcription was running
    assert ticks >= 8, f"event loop should have ticked during transcription, got {ticks} ticks"


@pytest.mark.asyncio
async def test_the_event_loop_keeps_turning_during_a_call() -> None:
    """Verify the event loop is not blocked: ticks occur during slow transcription."""
    slow_return_time = 0.0

    def slow(_: Waveform) -> Transcription:
        nonlocal slow_return_time
        time.sleep(0.1)
        slow_return_time = time.monotonic()
        return empty()

    transcriber = Transcriber(transcribe=slow, warm_up=lambda: None)
    tick_times: list[float] = []
    slow_start = 0.0

    async def ticker() -> None:
        nonlocal tick_times
        for _ in range(15):
            await asyncio.sleep(0.01)
            tick_times.append(time.monotonic())

    # Run slow transcription and ticker concurrently
    slow_start = time.monotonic()
    await asyncio.gather(transcriber.run(waveform()), ticker())

    # Verify we got several ticks during the slow call
    assert len(tick_times) >= 8, f"should have gotten 8+ ticks, got {len(tick_times)}"

    # Key assertion: first tick must occur BEFORE the slow() function returns.
    # With threading, the event loop continues while slow() sleeps, so ticker()
    # can run. Without threading, the event loop is blocked during sleep().
    slow_duration = slow_return_time - slow_start
    first_tick_time = tick_times[0] - slow_start
    assert tick_times[0] < slow_return_time, (
        f"first tick should occur during slow() (before {slow_duration:.3f}s), "
        f"but occurred at {first_tick_time:.3f}s"
    )

    # Verify the ticks span a significant range
    tick_span = tick_times[-1] - tick_times[0]
    assert tick_span >= 0.08, f"ticks should span 0.08s+, got {tick_span:.3f}s"


@pytest.mark.asyncio
async def test_warm_up_runs_once_and_its_failure_is_the_callers() -> None:
    calls = 0

    def load() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("no token")

    transcriber = Transcriber(transcribe=lambda _: empty(), warm_up=load)

    with pytest.raises(RuntimeError, match="no token"):
        await transcriber.warm_up()
    await transcriber.warm_up()
    await transcriber.warm_up()

    assert calls == 2


@pytest.mark.asyncio
async def test_no_arguments_uses_the_live_pipeline_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Transcriber()`` with no overrides wires to the live model, not the
    stored one -- never loading a real model here."""
    warm_up_calls = 0
    transcribe_calls: list[Waveform] = []

    def fake_warm_up_live() -> None:
        nonlocal warm_up_calls
        warm_up_calls += 1

    def fake_transcribe_live(waveform_: Waveform, *, glossary: str = "") -> Transcription:
        transcribe_calls.append(waveform_)
        return empty()

    monkeypatch.setattr(pipeline, "warm_up_live", fake_warm_up_live)
    monkeypatch.setattr(pipeline, "transcribe_live", fake_transcribe_live)
    # Whatever this machine has, the default engine under test is CTranslate2.
    monkeypatch.setattr(backends, "mlx_available", lambda: False)

    transcriber = Transcriber()
    await transcriber.warm_up()
    result = await transcriber.run(waveform())

    assert warm_up_calls == 1
    assert len(transcribe_calls) == 1
    assert result == empty()
