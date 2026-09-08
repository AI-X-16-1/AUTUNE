"""Celery tasks for module A.

A is the producer: it turns a recording into a transcript, deletes the raw
audio, and publishes TranscriptReady. See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from celery import shared_task

from autune_core import get_logger

log = get_logger(__name__)


@shared_task(name="autune.audio.process_recording", acks_late=True)
def process_recording(meeting_id: str, upload_path: str) -> None:
    """Transcribe a recording, then delete it.

    The recording must be deleted in a ``finally`` block so it goes on success,
    on exception and on cancellation alike. See docs/architecture/privacy.md.
    """
    log.info("audio_process_started", meeting_id=meeting_id)
    # TODO(김민경): VAD -> STT -> diarization -> identification -> PII masking
    #   -> delete raw audio in finally -> persist utterances
    #   -> publish TranscriptReady with original_audio_deleted=True.
