"""One utterance in, one unit vector out -- with pyannote and torch faked.

Neither test loads a model. ``pyannote.audio`` and ``torch`` are stand-ins
installed into ``sys.modules`` so the file runs on CI with no weights, no
token and no GPU, the way ``test_diarization_hints`` fakes the pipeline.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from autune_audio.live import embedder as embedder_module
from autune_audio.live.embedder import CHECKPOINT, MIN_SECONDS, Embedder
from autune_audio.schemas import SAMPLE_RATE, Waveform

Seen = list[dict[str, object]]


class _Tensor:
    def __init__(self, array: np.ndarray) -> None:
        self.array = array

    def unsqueeze(self, dim: int) -> _Tensor:
        return _Tensor(self.array[None, :])


def install_fakes(monkeypatch: pytest.MonkeyPatch, *, output: np.ndarray, seen: Seen) -> None:
    def from_pretrained(checkpoint: str, **kwargs: object) -> object:
        seen.append({"checkpoint": checkpoint, **kwargs})
        return object()

    class Inference:
        def __init__(self, model: object, *, window: str) -> None:
            seen.append({"window": window})

        def __call__(self, file: dict[str, object]) -> np.ndarray:
            waveform = file["waveform"]
            assert isinstance(waveform, _Tensor)
            seen.append({"samples": waveform.array.shape[1], "sample_rate": file["sample_rate"]})
            return output

    fake_pyannote = types.SimpleNamespace(
        Inference=Inference, Model=types.SimpleNamespace(from_pretrained=from_pretrained)
    )
    monkeypatch.setitem(sys.modules, "pyannote", types.SimpleNamespace(audio=fake_pyannote))
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_pyannote)
    monkeypatch.setitem(
        sys.modules, "torch", types.SimpleNamespace(from_numpy=lambda a: _Tensor(a))
    )


def test_the_output_is_a_unit_vector_of_the_model_width(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Seen = []
    install_fakes(monkeypatch, output=np.full(256, 0.5, dtype=np.float32), seen=seen)

    vector = Embedder(token="hf_x").embed(
        Waveform(samples=np.zeros(SAMPLE_RATE * 2, dtype=np.float32))
    )

    assert vector.shape == (256,)
    assert vector.dtype == np.float32
    assert np.linalg.norm(vector) == pytest.approx(1.0)
    assert seen[0] == {"checkpoint": CHECKPOINT, "token": "hf_x"}
    assert seen[1] == {"window": "whole"}
    assert seen[2] == {"samples": SAMPLE_RATE * 2, "sample_rate": SAMPLE_RATE}


def test_an_empty_token_is_passed_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Seen = []
    install_fakes(monkeypatch, output=np.ones(256, dtype=np.float32), seen=seen)
    Embedder().warm_up()
    assert seen[0] == {"checkpoint": CHECKPOINT, "token": None}


def test_a_short_utterance_is_padded_to_the_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Seen = []
    install_fakes(monkeypatch, output=np.ones(256, dtype=np.float32), seen=seen)
    Embedder().embed(Waveform(samples=np.zeros(SAMPLE_RATE // 10, dtype=np.float32)))  # 100 ms
    assert seen[2]["samples"] == int(MIN_SECONDS * SAMPLE_RATE)


def test_the_model_is_loaded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Seen = []
    install_fakes(monkeypatch, output=np.ones(256, dtype=np.float32), seen=seen)
    e = Embedder()
    e.warm_up()
    e.warm_up()
    e.embed(Waveform(samples=np.zeros(SAMPLE_RATE, dtype=np.float32)))
    assert sum(1 for s in seen if "checkpoint" in s) == 1


def test_a_zero_vector_is_an_error_not_a_label(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fakes(monkeypatch, output=np.zeros(256, dtype=np.float32), seen=[])
    with pytest.raises(ValueError):
        Embedder().embed(Waveform(samples=np.zeros(SAMPLE_RATE, dtype=np.float32)))


def test_a_load_failure_propagates_from_warm_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pyannote", None)
    monkeypatch.setitem(sys.modules, "pyannote.audio", None)
    with pytest.raises(ImportError):
        Embedder().warm_up()


def test_a_missing_model_raises_instead_of_reaching_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: Seen = []
    install_fakes(monkeypatch, output=np.ones(256, dtype=np.float32), seen=seen)
    monkeypatch.setattr(
        sys.modules["pyannote.audio"].Model, "from_pretrained", lambda checkpoint, **kwargs: None
    )
    with pytest.raises(RuntimeError):
        Embedder().warm_up()


def test_torch_is_not_imported_at_module_scope() -> None:
    assert "torch" not in vars(embedder_module)
    assert "pyannote" not in vars(embedder_module)
