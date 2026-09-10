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
from .quality import detect_repetition
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


def transcribe(waveform: Waveform, *, language: str | None = "ko") -> Transcription:
    """Transcribe a decoded waveform.

    ``language`` is pinned to Korean by default rather than detected: detection
    on a short or noisy opening picks the wrong language and the whole meeting
    comes back as nonsense. Pass ``None`` to detect.

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
    # Measured here rather than at the publish step so it lands in the log next
    # to the run that produced it: the glossary and the decoder settings are the
    # things that cause a collapse, and they are visible from here.
    repetition = detect_repetition(transcription)
    log.info(
        "whisper_transcribed",
        segments=len(segments),
        words=len(transcription.words),
        seconds=round(waveform.duration, 1),
        language=info.language,
        distinct_ratio=round(repetition.distinct_ratio, 3),
        longest_repeat_run=repetition.longest_repeat_run,
    )
    if repetition.collapsed:
        log.error("whisper_collapsed", segments=repetition.segments)
    return transcription


def transcribe_file(path: Path, *, language: str | None = "ko") -> Transcription:
    """Decode and transcribe in one step.

    The waveform stays in memory and is dropped when this returns. Deleting the
    source recording is the caller's job, in a ``finally`` block — see
    docs/architecture/privacy.md section 1.
    """
    return transcribe(decode(path), language=language)
