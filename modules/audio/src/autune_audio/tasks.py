"""Celery tasks for module A.

A is the producer: it turns a recording into a transcript, deletes the raw
audio, and publishes TranscriptReady. See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from pathlib import Path

from celery import shared_task

from autune_audio.storage import adopt
from autune_core import get_logger

log = get_logger(__name__)


@shared_task(name="autune.audio.process_recording", acks_late=True)
def process_recording(meeting_id: str, upload_path: str) -> None:
    """Transcribe a recording, then delete it.

    Deletion is not written here. ``storage.adopt`` owns it, so this task cannot
    forget it and cannot get it subtly different from the dev upload page.
    ``recording.deleted`` is what ``PrivacyFlags.original_audio_deleted`` is set
    from, and it is read from the filesystem rather than from having reached a
    line. See docs/architecture/privacy.md section 1.

    ``adopt`` rather than ``recording_on_disk``: the upload endpoint has already
    written ``upload_path``, and that file is the durable copy invariant 11
    cares about. Copying it into a temp file would leave the original behind
    after the copy was deleted — the leak looks fixed and is not.
    """
    log.info("audio_process_started", meeting_id=meeting_id)
    with adopt(Path(upload_path)) as recording:
        # TODO(김민경): VAD -> STT -> diarization -> identification -> PII
        #   masking -> persist utterances -> publish TranscriptReady with
        #   original_audio_deleted=recording.deleted (#6, #7, #9).
        log.info("audio_process_placeholder", meeting_id=meeting_id)
    log.info("audio_process_finished", meeting_id=meeting_id, deleted=recording.deleted)
