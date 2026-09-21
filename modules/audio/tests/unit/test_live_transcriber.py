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

from autune_audio.live.transcriber import Transcriber
from autune_audio.schemas import SAMPLE_RATE, Transcription, Waveform


def waveform(seconds: float = 0.5) -> Waveform:
    return Waveform(samples=np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32))


def empty() -> Transcription:
    return Transcription(segments=(), language="ko", language_probability=1.0, duration=0.5)


@pytest.mark.asyncio
async def test_two_calls_do_not_overlap() -> None:
    inside = 0
    peak = 0
    lock = threading.Lock()

    def slow(_: Waveform) -> Transcription:
        nonlocal inside, peak
        with lock:
            inside += 1
            peak = max(peak, inside)
        time.sleep(0.05)
        with lock:
            inside -= 1
        return empty()

    transcriber = Transcriber(transcribe=slow, warm_up=lambda: None)

    await asyncio.gather(transcriber.run(waveform()), transcriber.run(waveform()))

    assert peak == 1


@pytest.mark.asyncio
async def test_the_event_loop_keeps_turning_during_a_call() -> None:
    def slow(_: Waveform) -> Transcription:
        time.sleep(0.1)
        return empty()

    transcriber = Transcriber(transcribe=slow, warm_up=lambda: None)
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.01)
            ticks += 1

    await asyncio.gather(transcriber.run(waveform()), tick())

    assert ticks == 5


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
