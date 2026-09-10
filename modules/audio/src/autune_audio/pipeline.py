"""Speech to text.

Model loading and inference only — no database writes, no HTTP. The model is
loaded once per worker and reused; loading it per task would dominate the
processing-time metric.

This stage does not know about speakers. Diarization runs separately and the two
timelines are joined afterwards, which is why nothing here mentions one.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from faster_whisper import WhisperModel

from autune_core import get_logger

from .config import get_settings
from .decoding import decode
from .schemas import Segment, Transcription, Waveform, Word

log = get_logger(__name__)


@lru_cache(maxsize=1)
def _model() -> WhisperModel:
    """The transcription model, loaded once per process.

    ``compute_type`` follows the device: int8 is what makes CPU inference reach
    the 1.5x-realtime target, and float16 is the sane default on a GPU.
    """
    settings = get_settings()
    compute_type = "float16" if settings.device == "cuda" else "int8"
    log.info("whisper_loading", model=settings.whisper_model, device=settings.device)
    return WhisperModel(
        settings.whisper_model,
        device=settings.device,
        compute_type=compute_type,
        download_root=settings.model_cache or None,
    )


def _glossary_kwargs(glossary: str, mode: str) -> dict[str, str]:
    """Which of Whisper's two prompt channels carries the glossary.

    They are not interchangeable, and reading ``faster_whisper``'s ``get_prompt``
    says why:

    - ``initial_prompt`` is appended to ``previous_tokens``, which is truncated
      to the last 223 tokens. Decoded text keeps pushing into that window, so on
      a long recording the glossary is evicted by the transcript itself.
    - ``hotwords`` are re-prepended on every ``get_prompt`` call, so they last
      the whole file — but they are truncated from the *other* end.

    On a 165-second recording ``initial_prompt`` won clearly — term accuracy 18%
    to 82%, against 36% for ``hotwords`` — and that reading does not survive a
    meeting. Re-run on the 11m37s recording, whole transcript against whole
    reference:

    ======== ===== ==============
    variant  CER   term accuracy
    ======== ===== ==============
    none     0.157 9/29 = 31%
    prompt   0.232 8/29 = 28%
    hotwords 0.170 25/29 = 86%
    both     0.220 15/29 = 52%
    ======== ===== ==============

    The short file put its first technical term at 34 seconds, while the long
    one puts it at 148 — and 223 tokens is roughly 30 to 40 seconds of decoded
    Korean, so the prompt was gone before a single term was spoken. ``hotwords``
    is the only one of the two that reaches the end of a meeting.

    The CER cost of ``hotwords`` is 0.013, against nearly tripling term
    accuracy. That trade is the one this module exists to make: a wrong particle
    costs readability, a wrong entity name costs the action item attached to it.

    ``AUTUNE_AUDIO_GLOSSARY_MODE`` keeps the comparison runnable on a new model
    without a code change. See issue #118.
    """
    if not glossary:
        return {}
    if mode == "hotwords":
        return {"hotwords": glossary}
    if mode == "both":
        return {"initial_prompt": glossary, "hotwords": glossary}
    return {"initial_prompt": glossary}


def transcribe(
    waveform: Waveform, *, language: str | None = "ko", glossary: str = ""
) -> Transcription:
    """Transcribe a decoded waveform.

    ``language`` is pinned to Korean by default rather than detected: detection
    on a short or noisy opening picks the wrong language and the whole meeting
    comes back as nonsense. Pass ``None`` to detect.

    ``glossary`` is this meeting's vocabulary, from ``glossary.build_prompt``.
    Evaluation 01 measured term accuracy at 10/31 without it. How it is fed to
    the model is one decision, made in ``_glossary_kwargs`` — the two mechanisms
    behave differently over a long recording and the difference is measured
    rather than assumed.

    Word timestamps are always on. Speaker alignment needs them — a segment can
    span a turn change, and only word times say where to cut it.
    """
    settings = get_settings()
    segments_iter, info = _model().transcribe(
        waveform.samples,
        language=language,
        word_timestamps=True,
        vad_filter=True,
        beam_size=settings.beam_size,
        **_glossary_kwargs(glossary, settings.glossary_mode),
    )

    segments = tuple(
        Segment(
            start=s.start,
            end=s.end,
            text=s.text.strip(),
            words=tuple(
                Word(start=w.start, end=w.end, text=w.word.strip(), probability=w.probability)
                for w in (s.words or ())
            ),
        )
        for s in segments_iter
    )

    transcription = Transcription(
        segments=segments,
        language=info.language,
        language_probability=info.language_probability,
        duration=waveform.duration,
    )
    log.info(
        "whisper_transcribed",
        segments=len(segments),
        words=len(transcription.words),
        seconds=round(waveform.duration, 1),
        language=info.language,
    )
    return transcription


def transcribe_file(
    path: Path, *, language: str | None = "ko", glossary: str = ""
) -> Transcription:
    """Decode and transcribe in one step.

    The waveform stays in memory and is dropped when this returns. Deleting the
    source recording is ``storage.recording_on_disk``'s job — see
    docs/architecture/privacy.md section 1.
    """
    return transcribe(decode(path), language=language, glossary=glossary)
