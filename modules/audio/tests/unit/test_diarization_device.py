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
    """What ``torch.device(name)`` returns; pyannote wants the object, not a str.

    It refuses a name torch would refuse. Real ``torch.device('gpu')`` raises a
    ``RuntimeError``, and that is the whole mechanism keeping a typo from
    downloading half a gigabyte before it is noticed — a fake that accepted
    anything would let that regress.
    """

    KINDS = frozenset({"cpu", "cuda", "mps", "xpu"})

    def __init__(self, name: str) -> None:
        if name.split(":", 1)[0] not in self.KINDS:
            raise RuntimeError(f"Expected one of {sorted(self.KINDS)}, got '{name}'")
        self.name = name

    def __str__(self) -> str:
        # `str(torch.device('mps'))` is 'mps'; the load log line records it.
        return self.name

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
    """Settings for one test, built from the keywords alone.

    ``hf_token`` defaults to a value because ``AudioSettings`` refuses to load
    when a cuda device has no token, and a test about device *resolution*
    should not have to know that. A test that wants the empty token passes
    ``hf_token=""`` explicitly.

    ``_env_file=None`` is the part that matters for CI. ``AudioSettings``
    declares ``env_file=".env"``, so without this the values come partly from
    whatever the developer has in their own ``.env`` -- which is how
    ``test_a_device_index_is_checked_by_its_kind`` passed on a laptop with a
    Hugging Face token exported and failed in CI, where there is no file. A
    unit test's settings must come from its own arguments and nowhere else.
    """
    settings.setdefault("hf_token", "hf_x")
    monkeypatch.setattr(
        diarization,
        "get_settings",
        lambda: AudioSettings(_env_file=None, **settings),  # type: ignore[arg-type,call-arg]
    )


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


def test_an_inherited_device_torch_cannot_reach_takes_cpu_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AUTUNE_AUDIO_DEVICE=cuda`` with a CPU torch wheel is a working deployment.

    faster-whisper reaches the GPU through CTranslate2's own CUDA, so that
    setting means something even where ``torch.cuda.is_available()`` is False
    (@kjfcvx12, RTX 3060, #394). Raising here would fail every meeting on a box
    that changed no setting of its own; diarization has been on CPU there all
    along, so taking CPU changes nothing but the silence.
    """
    install_fakes(monkeypatch, cuda=False)
    use_settings(monkeypatch, device="cuda", hf_token="hf_x")
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        diarization.log, "warning", lambda event, **kw: seen.append({"event": event, **kw})
    )

    device = diarization.resolve_device()

    assert device.name == "cpu"
    line = next(entry for entry in seen if entry["event"] == "diarization_device_unavailable")
    assert line["requested"] == "cuda"
    assert line["using"] == "cpu"
    assert line["inherited_from"] == "AUTUNE_AUDIO_DEVICE"
    # The way out of the silence is a variable, so the line has to name it.
    assert "AUTUNE_AUDIO_DIARIZATION_DEVICE" in str(line["hint"])


def test_an_explicit_device_torch_cannot_reach_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the pair above: asking for it is a promise."""
    pipeline, loaded = install_fakes(monkeypatch, cuda=False)
    use_settings(monkeypatch, device="cpu", diarization_device="cuda", hf_token="hf_x")

    with pytest.raises(ConfigurationError) as caught:
        diarization.resolve_device()

    assert "AUTUNE_AUDIO_DIARIZATION_DEVICE" in str(caught.value)
    assert "AUTUNE_AUDIO_DIARIZATION_DEVICE=cpu" in str(caught.value)
    assert pipeline.moved_to == []
    assert loaded == []


def test_an_inherited_name_torch_does_not_know_takes_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same asking/inheriting split, applied to the name and not just its
    availability.

    `device` belongs to the transcriber, and faster-whisper has words torch does
    not -- `auto` is the obvious one. Refusing it would fail every meeting over
    a value this setting was never given, which is the shape of the bug review
    caught in the availability check (@PARKJAEKYUNG0525 on #394). Nobody typed
    it here, so nobody is told they cannot have it; CPU, and a line saying so.
    """
    install_fakes(monkeypatch, cuda=True, mps=True)
    use_settings(monkeypatch, device="auto", hf_token="hf_x")
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        diarization.log, "warning", lambda event, **kw: seen.append({"event": event, **kw})
    )

    device = diarization.resolve_device()

    assert device.name == "cpu"
    line = next(e for e in seen if e["event"] == "diarization_device_unavailable")
    assert line["requested"] == "auto"
    assert line["inherited_from"] == "AUTUNE_AUDIO_DEVICE"


def test_a_name_torch_does_not_know_is_refused_before_the_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``gpu``/``auto`` pass the availability check, which knows only two kinds."""
    pipeline, loaded = install_fakes(monkeypatch, cuda=True, mps=True)
    use_settings(monkeypatch, diarization_device="gpu")

    with pytest.raises(ConfigurationError) as caught:
        _diarize(diarization.PyannoteDiarizer("fake/checkpoint", token=""))

    assert "gpu" in str(caught.value)
    assert loaded == []
    assert pipeline.moved_to == []


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
