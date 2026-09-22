"""Which engine a live utterance goes to, and the mlx result in our shape.

Neither test loads a model: ``mlx_whisper`` is a fake module here, so the
file runs on the Linux CI where the extra does not resolve.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from autune_audio.config import AudioSettings
from autune_audio.live import backends
from autune_audio.schemas import SAMPLE_RATE, Waveform


def test_auto_is_mlx_only_where_it_can_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backends, "mlx_available", lambda: True)
    assert backends.resolve("auto") == "mlx"
    monkeypatch.setattr(backends, "mlx_available", lambda: False)
    assert backends.resolve("auto") == "faster_whisper"


def test_forcing_mlx_where_it_cannot_run_is_an_error_not_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backends, "mlx_available", lambda: False)
    with pytest.raises(RuntimeError, match="extra mlx"):
        backends.resolve("mlx")
    assert backends.resolve("faster_whisper") == "faster_whisper"


def test_the_mlx_result_becomes_a_transcription(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_transcribe(audio: np.ndarray, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "text": " 안녕하세요.",
            "language": "ko",
            "segments": [
                {
                    "start": np.float64(0.0),
                    "end": np.float64(1.2),
                    "text": " 안녕하세요.",
                    "words": [
                        {
                            "word": " 안녕하세요.",
                            "start": np.float64(0.0),
                            "end": np.float64(1.2),
                            "probability": 0.91,
                        }
                    ],
                }
            ],
        }

    monkeypatch.setitem(
        sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=fake_transcribe)
    )
    monkeypatch.setattr(
        backends, "get_settings", lambda: AudioSettings(live_mlx_model="mlx-community/whisper-tiny")
    )

    result = backends.transcribe_mlx(
        Waveform(samples=np.zeros(SAMPLE_RATE * 2, dtype=np.float32)), glossary="pgvector"
    )

    assert [s.text for s in result.segments] == ["안녕하세요."]
    assert result.words[0].probability == 0.91
    assert result.duration == 2.0
    assert calls[0]["path_or_hf_repo"] == "mlx-community/whisper-tiny"
    assert calls[0]["language"] == "ko"
    assert calls[0]["initial_prompt"] == "pgvector"
    assert calls[0]["temperature"] == 0.0  # no fallback retries on a fragment
    assert calls[0]["word_timestamps"] is True
