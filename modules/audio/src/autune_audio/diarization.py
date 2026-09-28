"""Where the voice changes. Separation only — putting a name to it comes later.

A Protocol rather than an import, the same seam module B put in front of its
classifier and D in front of its embedder. It is what lets ``speakers`` — the
part with the actual reasoning in it — be tested without a GPU, without a
Hugging Face token, and without the three gated repositories pyannote needs.

**Nothing here leaves our infrastructure.** The audio is the recording itself,
which invariant 11 keeps on one machine for the length of one task. There is no
hosted option and no config string that would add one; a diarizer runs in this
process or on our own inference server.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Protocol, runtime_checkable

from autune_core import get_logger
from autune_core.errors import ConfigurationError

from .config import get_settings
from .schemas import Turn, Waveform

log = get_logger(__name__)


def resolve_device() -> str:
    """Which device pyannote is loaded onto, or an error saying why it cannot be.

    ``diarization_device`` when it is set, ``device`` otherwise, so a CUDA
    deployment gets the GPU for both stages from the one variable it already
    sets and nothing changes for a deployment that sets neither.

    **An unavailable device raises instead of falling back to CPU.** CPU works,
    which is what makes the fallback tempting and what makes it wrong: it is
    14.3x slower (``config.AudioSettings.diarization_device``), and a warning in
    a log nobody reads is how a 14x regression ships. ``HISTORY.md`` section 4 is
    a list of failures that looked like successes until somebody measured; a
    diarizer quietly on CPU is the same shape, and the shape this refuses.
    """
    import torch  # noqa: PLC0415 - see PyannoteDiarizer; the extra may be absent

    settings = get_settings()
    requested = settings.diarization_device.strip()
    setting = "AUTUNE_AUDIO_DIARIZATION_DEVICE" if requested else "AUTUNE_AUDIO_DEVICE"
    device = requested or settings.device
    # Only the two accelerators are checked. `cpu` is always there, and a name
    # torch understands that this does not (`cuda:1`, `xpu`) is better refused by
    # torch, with its own message, than by a list here that would go stale.
    checks: dict[str, Callable[[], bool]] = {
        "mps": torch.backends.mps.is_available,
        "cuda": torch.cuda.is_available,
    }
    available = checks.get(device.split(":", 1)[0])
    if available is not None and not available():
        raise ConfigurationError(
            f"{setting} asks diarization to run on '{device}', which is not "
            f"available in this process. This is not falling back to CPU: CPU is "
            f"14x slower on the same recording, and a fallback would hide that. "
            f"Set AUTUNE_AUDIO_DIARIZATION_DEVICE=cpu to accept the cost."
        )
    return device


@runtime_checkable
class Diarizer(Protocol):
    """Split a waveform into turns. Labels are local to one recording."""

    @property
    def model_version(self) -> str:
        """Pinned, and recorded with every turn this produces.

        Speaker labels are only comparable within one model version: the same
        voice gets a different label from a different checkpoint, and an
        embedding from one is not in the same space as an embedding from
        another. Identification joins across meetings, so it has to know.
        """
        ...

    def diarize(self, waveform: Waveform) -> tuple[Turn, ...]:
        """Turns in time order. May leave gaps; **may not overlap**.

        Not a formality. The join reads the first turn containing a word, so
        overlapping turns silently hand an interruption to whoever started
        first — see ``PyannoteDiarizer.diarize`` for which of pyannote's two
        tracks satisfies this.
        """
        ...


class PyannoteDiarizer:
    """pyannote in this process. What a worker uses.

    ``pyannote.audio`` is imported inside the method that needs it. At module
    scope it would make ``apps/api`` load torch to answer a health check, and
    make this module's unit tests need a model.
    """

    def __init__(self, checkpoint: str, token: str) -> None:
        self._checkpoint = checkpoint
        self._token = token
        self._pipeline: object | None = None

    @property
    def model_version(self) -> str:
        return self._checkpoint

    def _load(self) -> object:
        if self._pipeline is not None:
            return self._pipeline
        try:
            from pyannote.audio import Pipeline  # noqa: PLC0415
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on the extra
            raise RuntimeError(
                "diarization needs the 'diarization' extra: "
                "uv sync --package autune-audio --extra diarization"
            ) from exc

        import torch  # noqa: PLC0415 - see the class docstring

        # Before the download, not after: a device this process cannot use is a
        # configuration error, and there is no reason to fetch half a gigabyte of
        # weights to find out.
        device = resolve_device()
        # `token=`, not `use_auth_token=`: renamed in pyannote.audio 4.x, and the
        # old name fails with a message about the wrong thing.
        pipeline = Pipeline.from_pretrained(self._checkpoint, token=self._token)
        if pipeline is None:
            # pyannote returns None rather than raising when it cannot load the
            # checkpoint -- an unaccepted licence on one of the three gated repos
            # is the usual reason. Without this the next line fails on `None`.
            raise RuntimeError(
                f"pyannote could not load '{self._checkpoint}'. Check "
                "AUTUNE_AUDIO_HF_TOKEN and that the licence is accepted on all "
                "three gated repositories (see config.AudioSettings.hf_token)."
            )
        # Never leave this out. `from_pretrained` builds the pipeline on CPU and
        # stays there, so without `.to()` a CUDA box runs Whisper on the GPU and
        # diarization beside it on the processor -- which is how it shipped until
        # #389's follow-up measured the 14.3x. Once, at load: `diarize` is called
        # per recording and moving a loaded model per call would cost more than
        # the device saves.
        pipeline.to(torch.device(device))
        self._pipeline = pipeline
        log.info("diarizer_loaded", checkpoint=self._checkpoint, device=device)
        return self._pipeline

    def diarize(self, waveform: Waveform) -> tuple[Turn, ...]:
        import torch  # noqa: PLC0415

        pipeline = self._load()
        # In memory, as a tensor. Writing a temp wav would put a second copy of
        # the recording on disk outside `storage`'s guarantee.
        audio = {
            "waveform": torch.from_numpy(waveform.samples).unsqueeze(0),
            "sample_rate": waveform.sample_rate,
        }
        # A speaker count, when the meeting knows one, is the one input that
        # stops pyannote splitting a voice across clusters on poor audio
        # (#325): the clustering step cannot invent a fourth speaker for a
        # room of one. Unset, it clusters freely, as the evaluation measured.
        bounds = get_settings().speaker_bounds()
        output = pipeline(audio, **bounds)  # type: ignore[operator]
        # `exclusive_speaker_diarization`, not `speaker_diarization`. pyannote
        # keeps both: the first is what it calls "adapted to downstream
        # transcription" and holds no overlapping turns, the second holds them.
        #
        # With overlaps, `speakers.speaker_at` returns the first turn containing
        # a word's midpoint -- whoever started earlier -- so an interruption is
        # absorbed into the speech it interrupted. "아니요" said over somebody
        # becomes part of their sentence, which is the misattribution this whole
        # join exists to prevent. DER is still scored against the overlapping
        # track, because that is what the metric is defined over.
        turns = tuple(
            Turn(start=float(segment.start), end=float(segment.end), speaker=str(label))
            for segment, _, label in output.exclusive_speaker_diarization.itertracks(
                yield_label=True
            )
        )
        log.info("diarized", turns=len(turns), speakers=len({t.speaker for t in turns}), **bounds)
        return turns


class FakeDiarizer:
    """Fixed turns, no model. What the tests run.

    Not an approximation of pyannote's accuracy and must not be used to estimate
    it. It exists so everything downstream of diarization can be built and
    measured before the model is wired.
    """

    model_version = "fake"

    def __init__(self, turns: tuple[Turn, ...] = ()) -> None:
        self._turns = turns

    def diarize(self, waveform: Waveform) -> tuple[Turn, ...]:
        return self._turns


@lru_cache(maxsize=1)
def get_diarizer() -> Diarizer:
    """The configured diarizer, loaded once per process.

    The checkpoint is hundreds of megabytes; a per-task load would dominate the
    pipeline.
    """
    settings = get_settings()
    if settings.diarization_model == "fake":
        return FakeDiarizer()
    return PyannoteDiarizer(settings.diarization_model, settings.require_hf_token())
