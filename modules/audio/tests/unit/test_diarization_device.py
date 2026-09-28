"""The diarization pipeline is moved onto a device, and the move is asserted.

``pyannote.audio`` and ``torch`` are stand-ins in ``sys.modules``, the way
``test_diarization_hints`` fakes the pipeline and ``test_live_embedder`` fakes
the loader: nothing here downloads weights, needs a token or needs a GPU.

The assertion that matters is ``.to()`` -- the call that was missing. A test
that only read the setting back would have passed against the bug.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator

import numpy as np
import pytest

from autune_audio import diarization
from autune_audio.config import AudioSettings
from autune_audio.schemas import SAMPLE_RATE, Turn, Waveform
from autune_core.errors import ConfigurationError


class _Device:
    """What ``torch.device(name)`` returns; pyannote wants the object, not a str."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - only for a failed assert
        return f"device({self.name})"


class _Track:
    def itertracks(self, yield_label: bool = False) -> Iterator[object]:
        return iter(())


class _Output:
    exclusive_speaker_diarization = _Track()


class _Pipeline:
    """Records every device it was moved to, and how often it was loaded."""

    def __init__(self) -> None:
        self.moved_to: list[_Device] = []

    def to(self, device: _Device) -> _Pipeline:
        self.moved_to.append(device)
        return self

    def __call__(self, audio: object, **kwargs: object) -> _Output:
        return _Output()


def install_fakes(
    monkeypatch: pytest.MonkeyPatch, *, mps: bool = False, cuda: bool = False
) -> tuple[_Pipeline, list[str]]:
    """A fake ``pyannote.audio`` and ``torch``, with the two accelerators dialled."""
    pipeline = _Pipeline()
    loaded: list[str] = []

    def from_pretrained(checkpoint: str, **kwargs: object) -> _Pipeline:
        loaded.append(checkpoint)
        return pipeline

    fake_pyannote = types.SimpleNamespace(
        Pipeline=types.SimpleNamespace(from_pretrained=from_pretrained)
    )
    monkeypatch.setitem(sys.modules, "pyannote", types.SimpleNamespace(audio=fake_pyannote))
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_pyannote)
    fake_torch = types.SimpleNamespace(
        device=_Device,
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps)),
        cuda=types.SimpleNamespace(is_available=lambda: cuda),
        from_numpy=lambda a: types.SimpleNamespace(unsqueeze=lambda d: a),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    return pipeline, loaded


def use_settings(monkeypatch: pytest.MonkeyPatch, **settings: object) -> None:
    monkeypatch.setattr(diarization, "get_settings", lambda: AudioSettings(**settings))  # type: ignore[arg-type]


def _diarize(diarizer: diarization.PyannoteDiarizer) -> None:
    diarizer.diarize(Waveform(samples=np.zeros(SAMPLE_RATE, dtype=np.float32)))


def test_an_unset_setting_follows_device(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline, _ = install_fakes(monkeypatch, cuda=True)
    use_settings(monkeypatch, device="cuda", hf_token="hf_x")

    _diarize(diarization.PyannoteDiarizer("fake/checkpoint", token="hf_x"))

    assert [d.name for d in pipeline.moved_to] == ["cuda"]


def test_an_explicit_device_wins_over_device(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline, _ = install_fakes(monkeypatch, mps=True)
    use_settings(monkeypatch, device="cpu", diarization_device="mps")

    _diarize(diarization.PyannoteDiarizer("fake/checkpoint", token=""))

    assert [d.name for d in pipeline.moved_to] == ["mps"]


def test_the_device_is_applied_once_however_many_recordings_arrive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, loaded = install_fakes(monkeypatch, mps=True)
    use_settings(monkeypatch, diarization_device="mps")
    d = diarization.PyannoteDiarizer("fake/checkpoint", token="")

    _diarize(d)
    _diarize(d)
    _diarize(d)

    assert len(loaded) == 1
    assert [d.name for d in pipeline.moved_to] == ["mps"]


def test_an_unavailable_device_raises_instead_of_running_on_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, loaded = install_fakes(monkeypatch, mps=False)
    use_settings(monkeypatch, diarization_device="mps")

    with pytest.raises(ConfigurationError) as caught:
        _diarize(diarization.PyannoteDiarizer("fake/checkpoint", token=""))

    assert "AUTUNE_AUDIO_DIARIZATION_DEVICE" in str(caught.value)
    assert "mps" in str(caught.value)
    # Not a fallback, and not a wasted download either.
    assert pipeline.moved_to == []
    assert loaded == []


def test_an_unavailable_cuda_names_the_setting_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fakes(monkeypatch, cuda=False)
    use_settings(monkeypatch, device="cuda", hf_token="hf_x")

    with pytest.raises(ConfigurationError) as caught:
        diarization.resolve_device()

    # `device` carried the request, so that is the variable to go and edit.
    assert "AUTUNE_AUDIO_DEVICE" in str(caught.value)
    assert "AUTUNE_AUDIO_DIARIZATION_DEVICE=cpu" in str(caught.value)


def test_a_device_index_is_checked_by_its_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """``cuda:1`` is a cuda request; the availability check must see past the index."""
    install_fakes(monkeypatch, cuda=False)
    use_settings(monkeypatch, diarization_device="cuda:1")

    with pytest.raises(ConfigurationError):
        diarization.resolve_device()


def test_cpu_is_always_available(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline, _ = install_fakes(monkeypatch, mps=False, cuda=False)
    use_settings(monkeypatch, diarization_device="cpu")

    _diarize(diarization.PyannoteDiarizer("fake/checkpoint", token=""))

    assert [d.name for d in pipeline.moved_to] == ["cpu"]


def test_a_checkpoint_that_will_not_load_raises_before_the_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pyannote returns ``None`` instead of raising when a licence is unaccepted."""
    install_fakes(monkeypatch, mps=True)
    monkeypatch.setattr(
        sys.modules["pyannote.audio"].Pipeline,
        "from_pretrained",
        lambda checkpoint, **kwargs: None,
    )
    use_settings(monkeypatch, diarization_device="mps")

    with pytest.raises(RuntimeError, match="AUTUNE_AUDIO_HF_TOKEN"):
        _diarize(diarization.PyannoteDiarizer("fake/checkpoint", token=""))


def test_the_load_log_line_names_the_device_that_ran(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fakes(monkeypatch, mps=True)
    use_settings(monkeypatch, diarization_device="mps")
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        diarization.log, "info", lambda event, **kw: seen.append({"event": event, **kw})
    )

    _diarize(diarization.PyannoteDiarizer("fake/checkpoint", token=""))

    loaded = next(line for line in seen if line["event"] == "diarizer_loaded")
    assert loaded["device"] == "mps"


def test_the_fake_diarizer_needs_no_device_and_no_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """It loads no model, so an unavailable accelerator is none of its business.

    ``torch`` is ``None`` in ``sys.modules``, which makes any import of it fail:
    a device resolved on this path would show up as an error, not as a pass.
    """
    monkeypatch.setitem(sys.modules, "torch", None)
    use_settings(monkeypatch, diarization_device="mps")
    turns = (Turn(start=0.0, end=1.0, speaker="A"),)
    fake: diarization.Diarizer = diarization.FakeDiarizer(turns)

    assert isinstance(fake, diarization.Diarizer)
    assert fake.diarize(Waveform(samples=np.zeros(SAMPLE_RATE, dtype=np.float32))) == turns
