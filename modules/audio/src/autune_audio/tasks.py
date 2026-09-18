"""Celery tasks for module A.

A is the producer: it turns a recording into a transcript, deletes the raw
audio, and publishes TranscriptReady. See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from celery import shared_task

from autune_audio import service
from autune_audio.decoding import decode
from autune_audio.diarization import get_diarizer
from autune_audio.glossary import build_prompt
from autune_audio.masking import mask
from autune_audio.persistence import persist_transcript, transcript_payload
from autune_audio.pipeline import transcribe
from autune_audio.quality import detect_repetition
from autune_audio.speakers import Utterance, assign_speakers
from autune_audio.storage import adopt
from autune_contracts.events import TRANSCRIPT_READY
from autune_core import get_logger
from autune_core.db import session_scope
from autune_core.events import publish

log = get_logger(__name__)


@shared_task(name="autune.audio.process_recording", acks_late=True)
def process_recording(meeting_id: str, upload_path: str) -> None:
    """Transcribe a recording, delete it, write it down, and announce it.

    The order is the whole task, and none of it is interchangeable.

    **Everything that needs the audio happens inside ``adopt``.** Deletion is
    not written here: ``storage.adopt`` owns it, in a ``finally``, so this task
    cannot forget it and cannot get it subtly different from the dev upload
    page. Success, exception and cancellation all delete it — invariant 11 does
    not bend for a failed job. ``recording.deleted`` is read from the
    filesystem rather than from having reached a line, which is why the write
    happens *after* the block: inside it, the flag is still False and would be
    published as a lie.

    One decode, two consumers. Whisper and pyannote both want the waveform, and
    decoding twice would double the slowest step that is not inference.

    **Masking runs before the write, not at it.** ``persist_transcript``
    re-checks and refuses, but a guard that is the only masker is a guard that
    fails closed on every real meeting. This is the line privacy.md section 2
    puts between transcription and the first ``INSERT``; the unmasked text is a
    local variable here and reaches no store, log or exception.

    **Publishing is outside the transaction and after it.** Four modules act on
    this event; publishing from inside would announce a meeting a rollback then
    erased. The payload is read back from the committed rows
    (``transcript_payload``) rather than assembled from what was computed.

    Safe to run twice, which ``acks_late`` makes a requirement rather than a
    nicety -- and ``apps/worker`` sets no ``visibility_timeout``, so Redis uses
    its default hour and a meeting past about 47 minutes at ~1.27x real time is
    redelivered *while the first run is still going*. ``persist_transcript``
    locks the meeting row for that, and consuming tasks are required to be
    idempotent for the same reason.

    **A redelivery arriving after the first run finished dies at ``adopt``**,
    because the upload is already deleted: there is nothing to decode and the
    job fails rather than republishing. That is the honest shape of a rerun
    here -- the transcript survives in the database, the audio does not, and
    invariant 11 does not bend to make a retry convenient. What the integration
    tests exercise is the *write* being safe to repeat, with ``decode`` faked;
    they do not claim the whole task replays.
    """
    log.info("audio_process_started", meeting_id=meeting_id)

    try:
        with adopt(Path(upload_path)) as recording:
            waveform = decode(recording.path)
            transcription = transcribe(waveform, glossary=build_prompt())
            turns = get_diarizer().diarize(waveform)

        # Before the write, not after: a collapsed transcript is not a
        # transcript, and the recording is already gone so there is nothing to
        # re-run.
        detect_repetition(transcription).raise_if_collapsed()

        spoken = assign_speakers(transcription, turns)
        masked = tuple(replace(utterance, text=mask(utterance.text).text) for utterance in spoken)
        _log_masking(meeting_id, spoken, masked)

        with session_scope() as session:
            persist_transcript(
                session,
                meeting_id=meeting_id,
                utterances=masked,
                duration_seconds=transcription.duration,
                audio_deleted=recording.deleted,
            )
            service.mark_complete(session, meeting_id=meeting_id)
            payload = transcript_payload(session, meeting_id=meeting_id)
    except Exception as error:
        # A fresh session: whatever went wrong may have left the one above
        # rolled back, and this write has to land regardless.
        with session_scope() as session:
            service.mark_failed(session, meeting_id=meeting_id)
        log.warning("audio_process_failed", meeting_id=meeting_id, error=type(error).__name__)
        raise

    publish(TRANSCRIPT_READY, payload.model_dump(mode="json"))
    log.info(
        "audio_process_finished",
        meeting_id=meeting_id,
        deleted=recording.deleted,
        utterances=len(payload.utterances),
        participants=len(payload.metadata.participants),
    )


def _log_masking(
    meeting_id: str, spoken: tuple[Utterance, ...], masked: tuple[Utterance, ...]
) -> None:
    """How many utterances changed. Never which, never what.

    `aud_masking_events` is where categories and counts belong; this line says
    only that the step ran, so a meeting where it silently did nothing is
    visible without putting transcript content in a log.
    """
    changed = sum(
        1 for before, after in zip(spoken, masked, strict=True) if before.text != after.text
    )
    log.info("transcript_masked", meeting_id=meeting_id, utterances=len(masked), changed=changed)
