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
from typing import Any

from faster_whisper import WhisperModel

from autune_core import get_logger

from .config import AudioSettings, get_settings
from .decoding import decode
from .quality import RepetitionReport, detect_repetition
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


def _decode(
    waveform: Waveform, *, language: str | None, settings: AudioSettings, **bias: Any
) -> Transcription:
    """One pass over the waveform. ``bias`` is whatever steers the decoder.

    Everything that nudges the model toward particular words goes through
    ``bias`` — today ``condition_on_previous_text``, and the meeting glossary
    when #119 lands. The fallback pass calls this with none of it, so a new kind
    of bias is dropped on retry without anyone remembering to drop it.
    """
    segments_iter, info = _model().transcribe(
        waveform.samples,
        language=language,
        word_timestamps=True,
        vad_filter=True,
        beam_size=settings.beam_size,
        **bias,
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
    return Transcription(
        segments=segments,
        language=info.language,
        language_probability=info.language_probability,
        duration=waveform.duration,
    )


def transcribe(waveform: Waveform, *, language: str | None = "ko") -> Transcription:
    """Transcribe a decoded waveform, retrying once if the decoder loops.

    ``language`` is pinned to Korean by default rather than detected: detection
    on a short or noisy opening picks the wrong language and the whole meeting
    comes back as nonsense. Pass ``None`` to detect.

    Word timestamps are always on. Speaker alignment needs them — a segment can
    span a turn change, and only word times say where to cut it.

    **The retry happens here because here is where the audio still is.** Whisper
    can lock onto a sentence and repeat it to the end of the file, and the
    recovery for that is a second pass with the decoder steered less: no
    glossary, and ``condition_on_previous_text`` off, which is what feeds a
    repetition back into itself. By the time the transcript reaches the publish
    step the recording has been deleted — ``storage.adopt`` deletes in a
    ``finally`` and invariant 11 does not bend for a failed job — so a retry
    from there would have nothing to read.

    A second collapse is returned rather than raised. This function reports what
    the decoder produced; refusing to publish it is
    ``RepetitionReport.raise_if_collapsed``'s job, at the boundary where that
    decision belongs.
    """
    settings = get_settings()
    transcription = _decode(waveform, language=language, settings=settings)
    repetition = detect_repetition(transcription)
    _log_transcription(transcription, repetition, attempt="first")

    if repetition.collapsed:
        log.warning(
            "whisper_collapsed_retrying",
            segments=repetition.segments,
            longest_repeat_run=repetition.longest_repeat_run,
        )
        transcription = _decode(
            waveform,
            language=language,
            settings=settings,
            condition_on_previous_text=False,
        )
        repetition = detect_repetition(transcription)
        _log_transcription(transcription, repetition, attempt="fallback")
        if repetition.collapsed:
            # Nothing further to try while the audio is here. The publish step
            # refuses it; this only records that both passes came apart.
            log.error("whisper_collapsed_twice", segments=repetition.segments)

    return transcription


def _log_transcription(
    transcription: Transcription, repetition: RepetitionReport, *, attempt: str
) -> None:
    """Counts and ratios only — a transcript is meeting content."""
    log.info(
        "whisper_transcribed",
        attempt=attempt,
        segments=len(transcription.segments),
        words=len(transcription.words),
        seconds=round(transcription.duration, 1),
        language=transcription.language,
        distinct_ratio=round(repetition.distinct_ratio, 3),
        longest_repeat_run=repetition.longest_repeat_run,
    )


def transcribe_file(path: Path, *, language: str | None = "ko") -> Transcription:
    """Decode and transcribe in one step.

    The waveform stays in memory and is dropped when this returns. Deleting the
    source recording is the caller's job, in a ``finally`` block — see
    docs/architecture/privacy.md section 1.
    """
    return transcribe(decode(path), language=language)
