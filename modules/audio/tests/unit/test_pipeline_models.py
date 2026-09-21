"""Which model, which threads, which beam.

The stored path and the live path are two different (model, thread count,
beam) triples, loaded once each and never confused for one another. This
patches ``pipeline.WhisperModel`` with a fake that records how it was built
and how it was asked to transcribe, so the assertions read off the real
call sites rather than trusting them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from autune_audio import pipeline
from autune_audio.config import AudioSettings
from autune_audio.schemas import SAMPLE_RATE, Waveform


class FakeWhisperModel:
    """Records its constructor kwargs and every ``transcribe`` call's kwargs."""

    instances: list[FakeWhisperModel] = []

    def __init__(self, name: str, **kwargs: Any) -> None:
        self.name = name
        self.init_kwargs = kwargs
        self.transcribe_calls: list[dict[str, Any]] = []
        FakeWhisperModel.instances.append(self)

    def transcribe(self, samples: Any, **kwargs: Any) -> tuple[Any, SimpleNamespace]:
        self.transcribe_calls.append(kwargs)
        info = SimpleNamespace(language="ko", language_probability=1.0, duration=0.0)
        return iter([]), info


@pytest.fixture(autouse=True)
def _fake_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "WhisperModel", FakeWhisperModel)
    FakeWhisperModel.instances = []
    pipeline._model_for.cache_clear()


def waveform(seconds: float = 1.0) -> Waveform:
    return Waveform(samples=np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32))


def test_stored_and_live_are_two_instances_with_their_own_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AudioSettings(live_whisper_model="large-v3-turbo", live_cpu_threads=10)
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)

    stored = pipeline._model()
    live = pipeline._live_model()

    assert stored is not live
    assert len(FakeWhisperModel.instances) == 2
    stored_fake, live_fake = FakeWhisperModel.instances
    assert stored_fake.name == settings.whisper_model
    assert stored_fake.init_kwargs["cpu_threads"] == 0
    assert stored_fake.init_kwargs["compute_type"] == "int8"
    assert live_fake.name == "large-v3-turbo"
    assert live_fake.init_kwargs["cpu_threads"] == 10
    assert live_fake.init_kwargs["compute_type"] == "int8"


def test_calling_each_twice_builds_nothing_new(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AudioSettings(live_whisper_model="large-v3-turbo", live_cpu_threads=10)
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)

    pipeline._model()
    pipeline._model()
    pipeline._live_model()
    pipeline._live_model()

    assert len(FakeWhisperModel.instances) == 2


def test_transcribe_live_uses_the_live_beam_and_transcribe_the_stored_beam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AudioSettings(
        live_whisper_model="large-v3-turbo", live_cpu_threads=10, live_beam_size=2, beam_size=5
    )
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)

    pipeline.transcribe_live(waveform())
    pipeline.transcribe(waveform())

    live_fake, stored_fake = FakeWhisperModel.instances
    assert live_fake.transcribe_calls[0]["beam_size"] == settings.live_beam_size
    assert stored_fake.transcribe_calls[0]["beam_size"] == settings.beam_size
