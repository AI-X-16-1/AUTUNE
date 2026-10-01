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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - torch is imported lazily at runtime
    import torch

from autune_core import get_logger
from autune_core.errors import ConfigurationError

from .config import get_settings
from .schemas import Turn, Waveform

log = get_logger(__name__)


def resolve_device() -> torch.device:
    """Which device pyannote is loaded onto, or an error saying why it cannot be.

    ``diarization_device`` when it is set, ``device`` otherwise, so a CUDA
    deployment gets the GPU for both stages from the one variable it already
    sets and nothing changes for a deployment that sets neither.

    **Asking for a device you do not have is an error. Inheriting one is not.**
    The two cases look identical here and are not the same claim:

    - ``AUTUNE_AUDIO_DIARIZATION_DEVICE=mps`` on a box without Metal is a
      deployment that asked for something and would not get it. It raises. CPU
      works, which is what makes a fallback tempting and what makes it wrong:
      it is 14.3x slower (``config.AudioSettings.diarization_device``), and a
      warning in a log nobody reads is how a 14x regression ships.
    - An empty setting inherits ``device``, which names the *transcriber's*
      device — and CTranslate2's idea of ``cuda`` is not torch's. On Windows,
      ``uv sync`` installs a CPU torch wheel while faster-whisper reaches the
      GPU through CTranslate2's own CUDA, so ``AUTUNE_AUDIO_DEVICE=cuda`` is a
      working configuration today with ``torch.cuda.is_available()`` False
      (@kjfcvx12 measured this on an RTX 3060, #394). Raising there would turn
      a deployment that works into one that fails every meeting, on an upgrade
      that changed no setting of its own. It takes CPU and says so.

    Nothing regresses on that second path: diarization has run on CPU since it
    shipped, so a box that inherits CPU here keeps exactly the speed it has.
    What it loses is the silence — ``diarization_device_unavailable`` names the
    variable to set. A deployment that wants the guarantee writes the device
    down, and then it is the first case.
    """
    import torch  # noqa: PLC0415 - see PyannoteDiarizer; the extra may be absent

    settings = get_settings()
    requested = settings.diarization_device.strip()
    setting = "AUTUNE_AUDIO_DIARIZATION_DEVICE" if requested else "AUTUNE_AUDIO_DEVICE"
    name = requested or settings.device
    # Only the two accelerators are checked, by the kind before the index, so
    # `cuda:1` is a cuda request. `cpu` is always there, and a device torch
    # understands that this does not (`xpu`, `mtia`) is better refused by torch,
    # with its own message, than by a list here that would go stale.
    checks: dict[str, Callable[[], bool]] = {
        "mps": torch.backends.mps.is_available,
        "cuda": torch.cuda.is_available,
    }
    available = checks.get(name.split(":", 1)[0])
    if available is not None and not available():
        if requested:
            raise ConfigurationError(
                f"{setting} asks diarization to run on '{name}', which is not "
                f"available in this process. This is not falling back to CPU: CPU is "
                f"14x slower on the same recording, and a fallback would hide that. "
                f"Set AUTUNE_AUDIO_DIARIZATION_DEVICE=cpu to accept the cost."
            )
        log.warning(
            "diarization_device_unavailable",
            inherited_from=setting,
            requested=name,
            using="cpu",
            hint=(
                "torch cannot reach this device, which is normal where the "
                "transcriber reaches it another way. Diarization runs on CPU, "
                "14x slower than an accelerator. Set "
                "AUTUNE_AUDIO_DIARIZATION_DEVICE to make this a decision."
            ),
        )
        name = "cpu"
    # Built here, not at the call site: `torch.device` is what refuses a name
    # torch does not know (`gpu`, `auto`), and refusing it before the caller
    # downloads half a gigabyte of weights is the whole point of resolving
    # early. Returning the object also leaves one place that knows the string.
    #
    # The same asking/inheriting split as above, for the same reason. An
    # unknown name somebody typed into `DIARIZATION_DEVICE` is a mistake to
    # report. An unknown name *inherited* from `device` is a value that belongs
    # to the transcriber -- faster-whisper takes `auto`, torch does not -- and
    # refusing it would fail meetings over a word this setting was never given
    # (@PARKJAEKYUNG0525 on #394).
    try:
        return torch.device(name)
    except (RuntimeError, TypeError, ValueError) as exc:
        if requested:
            raise ConfigurationError(
                f"{setting} is '{name}', which torch does not recognise as a device. "
                f"Use 'cpu', 'cuda', 'cuda:<n>' or 'mps'."
            ) from exc
        log.warning(
            "diarization_device_unavailable",
            inherited_from=setting,
            requested=name,
            using="cpu",
            hint=(
                "torch does not recognise this device name, which is normal "
                "where the transcriber has its own vocabulary. Diarization runs "
                "on CPU, 14x slower than an accelerator. Set "
                "AUTUNE_AUDIO_DIARIZATION_DEVICE to make this a decision."
            ),
        )
        return torch.device("cpu")


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

    def diarize(
        self, waveform: Waveform, *, on_progress: Callable[[float], None] | None = None
    ) -> tuple[Turn, ...]:
        """Turns in time order. May leave gaps; **may not overlap**.

        ``on_progress`` is told how far through the pass it is, 0..1, when the
        implementation can tell (``progress.ProgressReporter``).

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
        except ModuleNotFoundError as exc:  # pragma: no cover - a broken install
            # There is no 'diarization' extra to name: `pyannote-audio` is a
            # plain dependency of this module (@PARKJAEKYUNG0525 on #394, item
            # 8). Reaching here means the environment was not installed, not
            # that a feature was opted out of.
            raise RuntimeError(
                "pyannote.audio is not importable. It is a dependency of "
                "autune-audio, so this is an incomplete environment: "
                "uv sync --all-packages"
            ) from exc

        # Before the download, not after: a device this process cannot use is a
        # configuration error, and there is no reason to fetch half a gigabyte of
        # weights to find out. `process` resolves it earlier still, before it
        # decodes a recording (#394).
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
        # #394 measured the 14.3x. Once, at load: `diarize` is called per
        # recording and moving a loaded model per call would cost more than the
        # device saves.
        pipeline.to(device)
        self._pipeline = pipeline
        log.info("diarizer_loaded", checkpoint=self._checkpoint, device=str(device))
        return self._pipeline

    def diarize(
        self, waveform: Waveform, *, on_progress: Callable[[float], None] | None = None
    ) -> tuple[Turn, ...]:
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
        hook = {"hook": _progress_hook(on_progress)} if on_progress is not None else {}
        output = pipeline(audio, **bounds, **hook)  # type: ignore[operator]
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


_STEP_SPAN = {"segmentation": (0.0, 0.3), "embeddings": (0.3, 1.0)}
"""Where each of pyannote's two inference steps sits in the whole pass. The
embedding step runs a model per speech region and is most of the time; the
clustering in between reports no counts and is quick."""


def _progress_hook(on_progress: Callable[[float], None]) -> Callable[..., None]:
    """pyannote 4's ``hook``: called with a step name, and during inference
    with ``completed``/``total``. Folded into one rising fraction."""

    def hook(
        step_name: str,
        step_artifact: object = None,
        file: object = None,
        total: int | None = None,
        completed: int | None = None,
    ) -> None:
        span = _STEP_SPAN.get(step_name)
        if span is None or not total or completed is None:
            return
        start, end = span
        on_progress(start + (end - start) * min(1.0, completed / total))

    return hook


class FakeDiarizer:
    """Fixed turns, no model. What the tests run.

    Not an approximation of pyannote's accuracy and must not be used to estimate
    it. It exists so everything downstream of diarization can be built and
    measured before the model is wired.
    """

    model_version = "fake"

    def __init__(self, turns: tuple[Turn, ...] = ()) -> None:
        self._turns = turns

    def diarize(
        self, waveform: Waveform, *, on_progress: Callable[[float], None] | None = None
    ) -> tuple[Turn, ...]:
        if on_progress is not None:
            on_progress(1.0)
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
