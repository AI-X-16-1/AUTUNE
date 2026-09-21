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


def warm_up() -> None:
    """Load the model now rather than on the first transcription.

    The live channel calls this before it tells the browser it is ready, so a
    missing model or an unaccepted licence surfaces as a refused connection
    rather than as a stall on the first utterance.
    """
    _model()


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


def transcribe(
    waveform: Waveform, *, language: str | None = "ko", glossary: str = ""
) -> Transcription:
    """Transcribe a decoded waveform, retrying once if the decoder loops.

    ``language`` is pinned to Korean by default rather than detected: detection
    on a short or noisy opening picks the wrong language and the whole meeting
    comes back as nonsense. Pass ``None`` to detect.

    ``glossary`` is this meeting's vocabulary, from
    ``glossary.build_prompt(mode=settings.glossary_mode)`` — it has to be built
    for the channel it will travel on, because the two truncate from opposite
    ends. How it reaches the model is decided in ``_glossary_kwargs``.

    Word timestamps are always on. Speaker alignment needs them — a segment can
    span a turn change, and only word times say where to cut it.

    **The retry happens here because here is where the audio still is.** Whisper
    can lock onto a sentence and repeat it to the end of the file, and the
    recovery for that is a second pass with the decoder steered less: no
    glossary — one term in it was enough to cause a collapse on the evaluation
    recording — and ``condition_on_previous_text`` off, which is what feeds a
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
    transcription = _decode(
        waveform,
        language=language,
        settings=settings,
        **_glossary_kwargs(glossary, settings.glossary_mode),
    )
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


def transcribe_file(
    path: Path, *, language: str | None = "ko", glossary: str = ""
) -> Transcription:
    """Decode and transcribe in one step.

    The waveform stays in memory and is dropped when this returns. Deleting the
    source recording is the caller's job, in a ``finally`` block — see
    docs/architecture/privacy.md section 1.
    """
    return transcribe(decode(path), language=language, glossary=glossary)
