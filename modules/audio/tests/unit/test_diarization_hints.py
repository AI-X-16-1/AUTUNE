"""The speaker-count hint reaches pyannote, and only when set (#325).

The pipeline is a fake that records what it was called with; nothing here
loads a model or needs the extra.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator

import numpy as np
import pytest

from autune_audio import diarization
from autune_audio.config import AudioSettings
from autune_audio.schemas import SAMPLE_RATE, Waveform

Seen = list[dict[str, object]]


class _Track:
    def itertracks(self, yield_label: bool = False) -> Iterator[object]:
        return iter(())


class _Output:
    exclusive_speaker_diarization = _Track()


@pytest.fixture
def diarizer(monkeypatch: pytest.MonkeyPatch) -> tuple[diarization.PyannoteDiarizer, Seen]:
    seen: Seen = []

    def pipeline(audio: object, **kwargs: object) -> _Output:
        seen.append(dict(kwargs))
        return _Output()

    fake_torch = types.SimpleNamespace(
        from_numpy=lambda a: types.SimpleNamespace(unsqueeze=lambda d: a)
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    d = diarization.PyannoteDiarizer("fake/checkpoint", token="")
    d._pipeline = pipeline  # noqa: SLF001 - the seam under test is the call, not the load
    return d, seen


def _run(
    monkeypatch: pytest.MonkeyPatch,
    diarizer: tuple[diarization.PyannoteDiarizer, Seen],
    **settings: object,
) -> dict[str, object]:
    d, seen = diarizer
    monkeypatch.setattr(diarization, "get_settings", lambda: AudioSettings(**settings))  # type: ignore[arg-type]
    d.diarize(Waveform(samples=np.zeros(SAMPLE_RATE, dtype=np.float32)))
    return seen[-1]


def test_no_hint_means_pyannote_clusters_freely(
    monkeypatch: pytest.MonkeyPatch, diarizer: tuple[diarization.PyannoteDiarizer, Seen]
) -> None:
    assert _run(monkeypatch, diarizer) == {}


def test_an_exact_count_is_passed_as_num_speakers(
    monkeypatch: pytest.MonkeyPatch, diarizer: tuple[diarization.PyannoteDiarizer, Seen]
) -> None:
    assert _run(monkeypatch, diarizer, diarization_num_speakers=1) == {"num_speakers": 1}


def test_bounds_are_passed_when_there_is_no_exact_count(
    monkeypatch: pytest.MonkeyPatch, diarizer: tuple[diarization.PyannoteDiarizer, Seen]
) -> None:
    assert _run(monkeypatch, diarizer, diarization_min_speakers=2, diarization_max_speakers=4) == {
        "min_speakers": 2,
        "max_speakers": 4,
    }


def test_an_exact_count_wins_over_bounds(
    monkeypatch: pytest.MonkeyPatch, diarizer: tuple[diarization.PyannoteDiarizer, Seen]
) -> None:
    assert _run(monkeypatch, diarizer, diarization_num_speakers=3, diarization_max_speakers=5) == {
        "num_speakers": 3
    }
