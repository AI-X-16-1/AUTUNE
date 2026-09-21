"""Celery tasks for module A.

A is the producer: it turns a recording into a transcript, deletes the raw
audio, and publishes TranscriptReady. See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from dataclasses import replace

from celery import shared_task

from autune_audio import service
from autune_audio.config import get_settings
from autune_audio.decoding import decode
from autune_audio.diarization import get_diarizer
from autune_audio.glossary import build_prompt
from autune_audio.masking import mask
from autune_audio.persistence import persist_transcript, transcript_payload
from autune_audio.pipeline import transcribe
from autune_audio.quality import detect_repetition
from autune_audio.speakers import Utterance, assign_speakers
from autune_audio.storage import adopt, delete_orphan, upload_path
from autune_contracts.events import TRANSCRIPT_READY
from autune_core import get_logger
from autune_core.db import session_scope
from autune_core.events import publish

log = get_logger(__name__)


@shared_task(name="autune.audio.process_recording", acks_late=True)
def process_recording(job_id: str) -> None:
    """Transcribe a recording, delete it, write it down, and announce it.

    The order is the whole task, and none of it is interchangeable.

    **The argument is a job id, and the path is derived from it.** privacy.md
    section 1 forbids a path to raw audio in a Celery payload -- Celery writes
    arguments to the broker and to its failure output -- so the endpoint
    names the file after the job and this task asks ``storage.upload_path``
    where that is (#275). ``claim_job`` says which meeting, and whether this
    attempt is still the current one: a job a later upload superseded, or one
    that already finished, has its file deleted and is otherwise declined; one
    still running under another delivery is left entirely alone.

    **The sweep runs first.** Uploads whose task was lost after the enqueue
    have no other collector until there is a periodic trigger (#207); each
    run clears the ones the database says are over (``service.sweep_orphans``).

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

    **But a publish that fails still fails the meeting.** The transcript is
    committed and ``complete`` by then; if the broker then refuses the event,
    B, C, D and E never hear of the meeting, and a ``complete`` meeting
    refuses another upload -- a dead end (@kjfcvx12 on #259, item 4). So the
    publish has its own ``except``, calling ``mark_unannounced``: the rows
    stay (they are masked and correct), the meeting goes to ``failed``, and a
    re-upload is the recovery. Only the publish gets that treatment -- a
    failure in the bookkeeping *after* it means the consumers were told, and
    the meeting stays ``complete`` (@lsh2217 on #259).

    Safe to run twice, which ``acks_late`` makes a requirement rather than a
    nicety -- and ``apps/worker`` sets no ``visibility_timeout``, so Redis uses
    its default hour and a meeting past about 47 minutes at ~1.27x real time is
    redelivered *while the first run is still going*. ``persist_transcript``
    locks the meeting row for that, and consuming tasks are required to be
    idempotent for the same reason.

    **A redelivery arriving after the first run finished is declined at
    ``claim_job``**: the job is ``done``, the upload is already deleted, and
    there is nothing to decode. The transcript survives in the database, the
    audio does not, and invariant 11 does not bend to make a retry convenient.
    A redelivery arriving *while* the first run is still going is the case
    ``persist_transcript``'s row lock is for.
    """
    settings = get_settings()
    with session_scope() as session:
        claim = service.claim_job(session, job_id=job_id)
        service.sweep_orphans(session, settings=settings, keep=job_id)
    meeting_id = claim.meeting_id

    if not claim.run:
        if claim.owns_file:
            # Superseded or finished: nobody else will delete this attempt's
            # upload. A running first delivery keeps its own.
            delete_orphan(upload_path(job_id, settings))
        log.info("audio_process_declined", job_id=job_id, meeting_id=meeting_id)
        return

    log.info("audio_process_started", meeting_id=meeting_id, job_id=job_id)

    try:
        with adopt(upload_path(job_id, settings)) as recording:
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
            service.mark_complete(session, job_id=job_id)
            payload = transcript_payload(session, meeting_id=meeting_id)
    except Exception as error:
        # A fresh session: whatever went wrong may have left the one above
        # rolled back, and this write has to land regardless.
        with session_scope() as session:
            service.mark_failed(session, job_id=job_id)
        log.warning(
            "audio_process_failed",
            meeting_id=meeting_id,
            job_id=job_id,
            error=type(error).__name__,
        )
        raise

    # Three steps, three outcomes, and only the middle one may fail the
    # meeting. The publish is the moment the four consumers learn of it: if
    # *it* raises, nobody was told and the meeting must go back to failed so
    # a re-upload can. If the bookkeeping after it raises, they *were* told,
    # and turning the meeting red would invite a second announcement (#194).
    try:
        publish(TRANSCRIPT_READY, payload.model_dump(mode="json"))
    except Exception as error:
        with session_scope() as session:
            service.mark_unannounced(session, job_id=job_id)
        log.warning("audio_publish_failed", meeting_id=meeting_id, error=type(error).__name__)
        raise
    with session_scope() as session:
        service.mark_published(session, job_id=job_id)

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
