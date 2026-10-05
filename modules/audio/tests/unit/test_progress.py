"""How far a transcription has got, told to S12 while it runs.

``ProgressReporter`` is what the worker calls from inside Whisper's and
pyannote's loops, so it must be cheap when called thousands of times and must
never fail the job it is reporting on. The write is injected here; the real one
is an ``UPDATE aud_jobs`` (see the integration test).
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator

import numpy as np
import pytest

from autune_audio import diarization
from autune_audio.config import AudioSettings
from autune_audio.progress import ProgressReporter
from autune_audio.schemas import SAMPLE_RATE, Waveform

Writes = list[tuple[str, float | None]]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def recorded() -> tuple[ProgressReporter, Writes, Clock]:
    writes: Writes = []
    clock = Clock()
    reporter = ProgressReporter(
        "job_x", write=lambda stage, progress: writes.append((stage, progress)), clock=clock
    )
    return reporter, writes, clock


def test_entering_a_stage_is_written_at_once_at_zero(recorded) -> None:
    reporter, writes, _ = recorded
    reporter.stage("transcribing")
    assert writes == [("transcribing", 0.0)]


def test_updates_within_the_interval_are_held_back(recorded) -> None:
    reporter, writes, clock = recorded
    reporter.stage("transcribing")
    reporter.update(0.1)
    reporter.update(0.2)
    clock.now = 1.5
    reporter.update(0.3)
    assert writes == [("transcribing", 0.0), ("transcribing", 0.3)]


def test_a_change_under_one_percent_is_not_written(recorded) -> None:
    reporter, writes, clock = recorded
    reporter.stage("diarizing")
    clock.now = 5
    reporter.update(0.004)
    assert writes == [("diarizing", 0.0)]


def test_a_new_stage_is_written_even_inside_the_interval(recorded) -> None:
    reporter, writes, _ = recorded
    reporter.stage("transcribing")
    reporter.stage("diarizing")
    assert writes == [("transcribing", 0.0), ("diarizing", 0.0)]


def test_fractions_are_clamped_to_zero_and_one(recorded) -> None:
    reporter, writes, clock = recorded
    reporter.stage("transcribing")
    clock.now = 2
    reporter.update(1.7)
    clock.now = 4
    reporter.update(-3)
    assert writes[1:] == [("transcribing", 1.0), ("transcribing", 0.0)]


def test_an_update_before_any_stage_is_ignored(recorded) -> None:
    reporter, writes, clock = recorded
    clock.now = 9
    reporter.update(0.5)
    assert writes == []


def test_a_failing_write_never_reaches_the_job() -> None:
    def broken(stage: str, progress: float | None) -> None:
        raise RuntimeError("database went away")

    reporter = ProgressReporter("job_x", write=broken, clock=Clock())
    reporter.stage("transcribing")  # does not raise
    reporter.update(0.5)


# --- pyannote -------------------------------------------------------------


class _Track:
    def itertracks(self, yield_label: bool = False) -> Iterator[object]:
        return iter(())


class _Output:
    exclusive_speaker_diarization = _Track()


def _pyannote(monkeypatch: pytest.MonkeyPatch, calls: list[tuple]) -> diarization.PyannoteDiarizer:
    """A pipeline that calls its hook the way pyannote 4 does: a step name,
    then ``completed``/``total`` while the step's inference runs."""

    def pipeline(audio: object, hook=None, **kwargs: object) -> _Output:
        calls.append(("hook" if hook else "no hook",))
        if hook:
            hook("segmentation", None, completed=0, total=10)
            hook("segmentation", None, completed=10, total=10)
            hook("speaker_counting", object())
            hook("embeddings", None, completed=5, total=10)
            hook("embeddings", None, completed=10, total=10)
            hook("discrete_diarization", object())
        return _Output()

    fake_torch = types.SimpleNamespace(
        from_numpy=lambda a: types.SimpleNamespace(unsqueeze=lambda d: a)
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(diarization, "get_settings", lambda: AudioSettings())
    d = diarization.PyannoteDiarizer("fake/checkpoint", token="")
    d._pipeline = pipeline  # noqa: SLF001
    return d


def test_pyannote_steps_become_one_rising_fraction(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    seen: list[float] = []
    d = _pyannote(monkeypatch, calls)

    d.diarize(Waveform(samples=np.zeros(SAMPLE_RATE, dtype=np.float32)), on_progress=seen.append)

    assert seen == sorted(seen)
    assert seen[0] == 0.0
    assert seen[-1] == 1.0
    assert 0.0 < seen[1] < seen[-1]


def test_no_callback_passes_no_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    d = _pyannote(monkeypatch, calls)
    d.diarize(Waveform(samples=np.zeros(SAMPLE_RATE, dtype=np.float32)))
    assert calls == [("no hook",)]


# --- whisper ----------------------------------------------------------------


def test_whisper_progress_is_how_far_into_the_audio_the_last_segment_ended() -> None:
    from autune_audio import pipeline as whisper

    class Seg:
        def __init__(self, start: float, end: float) -> None:
            self.start, self.end, self.text, self.words = start, end, "x", ()

    class Model:
        def transcribe(self, samples, **_: object):
            info = types.SimpleNamespace(language="ko", language_probability=1.0, duration=10.0)
            return iter([Seg(0, 2.5), Seg(2.5, 5), Seg(5, 10)]), info

    seen: list[float] = []
    whisper._decode(  # noqa: SLF001
        Waveform(samples=np.zeros(SAMPLE_RATE * 10, dtype=np.float32)),
        language="ko",
        settings=AudioSettings(),
        model=Model(),  # type: ignore[arg-type]
        on_progress=seen.append,
    )
    assert seen == [0.25, 0.5, 1.0]


# --- guard (cancel/restart) ---


class StopError(Exception):
    pass


def _stopping_after(calls: int):
    seen = {"n": 0}

    def check() -> None:
        seen["n"] += 1
        if seen["n"] > calls:
            raise StopError

    return check


def test_every_update_asks_the_check_even_when_throttled() -> None:
    """The flag is cheap to read; throttling is for the write, not the check."""
    writes: list[tuple[str, float | None]] = []
    report = ProgressReporter(
        "job_1",
        write=lambda s, p: writes.append((s, p)),
        clock=lambda: 0.0,
        check=_stopping_after(2),
    )
    report.stage("transcribing")
    report.update(0.001)
    with pytest.raises(StopError):
        report.update(0.002)


def test_entering_a_stage_asks_the_check() -> None:
    report = ProgressReporter("job_1", write=lambda s, p: None, check=_stopping_after(0))
    with pytest.raises(StopError):
        report.stage("masking")


def test_a_stop_is_not_swallowed_like_a_failed_write() -> None:
    """Write failures are logged and dropped; a stop must reach the task."""

    def broken_write(stage: str, progress: float | None) -> None:
        raise RuntimeError("db down")

    report = ProgressReporter("job_1", write=broken_write, check=_stopping_after(1))
    report.stage("decoding")
    with pytest.raises(StopError):
        report.stage("transcribing")
