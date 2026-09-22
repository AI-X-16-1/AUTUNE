"""Which engine transcribes a live utterance, and the one that is not CTranslate2.

The stored path has one engine: faster-whisper, which follows ``device`` --
CPU, or CUDA where there is an NVIDIA GPU. The live path can afford a second
one because a row is display, remade by the stored path after the upload:

- ``faster_whisper`` -- the stored path's engine on the live model
  (``pipeline.transcribe_live``). On CUDA it is the fastest option there is.
- ``mlx`` -- ``mlx-whisper`` on Apple silicon's GPU through Metal, which
  CTranslate2 cannot use. Measured on an M4 Pro: 0.85 s for an 11 s Korean
  utterance against 2.4 s on ten CPU threads, same text.
- ``auto`` -- ``mlx`` where it is installed and can run, ``faster_whisper``
  everywhere else. The default, so a laptop with the extra installed gets
  the fast path without a setting and a Linux box gets what it always had.

``mlx-whisper`` is an optional extra of this module (``uv sync --package
autune-audio --extra mlx``), never a hard dependency: it only installs on
macOS arm64.
"""

from __future__ import annotations

import importlib.util
import platform
import sys
from collections.abc import Callable
from typing import Literal

import numpy as np

from autune_audio import pipeline
from autune_audio.config import get_settings
from autune_audio.glossary import build_prompt
from autune_audio.schemas import SAMPLE_RATE, Segment, Transcription, Waveform, Word
from autune_core import get_logger

log = get_logger(__name__)

Impl = Literal["auto", "faster_whisper", "mlx"]


def mlx_available() -> bool:
    """Apple silicon, and the extra is installed."""
    return (
        sys.platform == "darwin"
        and platform.machine() == "arm64"
        and importlib.util.find_spec("mlx_whisper") is not None
    )


def resolve(impl: Impl) -> Literal["faster_whisper", "mlx"]:
    if impl == "auto":
        return "mlx" if mlx_available() else "faster_whisper"
    if impl == "mlx" and not mlx_available():
        raise RuntimeError(
            "AUTUNE_AUDIO_LIVE_TRANSCRIBER_IMPL=mlx needs Apple silicon and the extra: "
            "uv sync --package autune-audio --extra mlx"
        )
    return impl


def select() -> tuple[Callable[[Waveform], Transcription], Callable[[], None]]:
    """The (transcribe, warm_up) pair for the configured live engine."""
    settings = get_settings()
    chosen = resolve(settings.live_transcriber_impl)
    log.info("live_transcriber_selected", impl=chosen)
    if chosen == "mlx":
        return transcribe_mlx, warm_up_mlx
    return (
        lambda waveform: pipeline.transcribe_live(waveform, glossary=build_prompt()),
        pipeline.warm_up_live,
    )


# --- mlx -------------------------------------------------------------------


def warm_up_mlx() -> None:
    """Load the weights and compile the graph on one second of silence, so the
    first real utterance does not pay for either."""
    transcribe_mlx(Waveform(samples=np.zeros(SAMPLE_RATE, dtype=np.float32)))


def transcribe_mlx(waveform: Waveform, *, glossary: str | None = None) -> Transcription:
    """One utterance through mlx-whisper, in the same ``Transcription`` shape.

    The glossary goes in as ``initial_prompt`` -- mlx-whisper has no
    ``hotwords`` -- so ``glossary_mode`` does not apply here. Language is
    pinned to Korean for the reason ``pipeline.transcribe`` gives.
    """
    import mlx_whisper  # the extra; see the module docstring

    settings = get_settings()
    prompt = build_prompt() if glossary is None else glossary
    result = mlx_whisper.transcribe(
        waveform.samples,
        path_or_hf_repo=settings.live_mlx_model,
        language="ko",
        word_timestamps=True,
        initial_prompt=prompt or None,
        # No temperature fallback: on a short, quiet fragment the retries at
        # higher temperature are where the invented words came from.
        temperature=0.0,
    )
    segments = tuple(
        Segment(
            start=float(s["start"]),
            end=float(s["end"]),
            text=str(s["text"]).strip(),
            words=tuple(
                Word(
                    start=float(w["start"]),
                    end=float(w["end"]),
                    text=str(w["word"]).strip(),
                    probability=float(w.get("probability", 0.0)),
                )
                for w in s.get("words") or ()
            ),
        )
        for s in result["segments"]
    )
    transcription = Transcription(
        segments=segments,
        language=str(result.get("language") or "ko"),
        language_probability=1.0,
        duration=waveform.duration,
    )
    pipeline.log_live_transcription(transcription)
    return transcription
