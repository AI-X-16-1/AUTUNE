"""Celery tasks for module A.

A is the producer: it turns a recording into a transcript, deletes the raw
audio, and publishes TranscriptReady. See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import sqlalchemy as sa
from celery import shared_task
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from autune_audio import service
from autune_audio.config import get_settings
from autune_audio.decoding import decode
from autune_audio.diarization import get_diarizer
from autune_audio.glossary import build_prompt
from autune_audio.live.embedder import Embedder
from autune_audio.masking import mask
from autune_audio.models import AudConsentAttestation, AudSpeakerEmbedding
from autune_audio.persistence import persist_transcript, transcript_payload
from autune_audio.pipeline import transcribe
from autune_audio.quality import detect_repetition
from autune_audio.schemas import Turn, Waveform
from autune_audio.speaker_audio import representative_waveform
from autune_audio.speakers import Utterance, assign_speakers, rename_speakers
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

    **A speaker's observation vector is also taken inside ``adopt``, not
    after -- and only for a consented meeting.** ``consented`` is read once,
    at the very first claim, from the same session that runs
    ``claim_job``/``sweep_orphans``; ``_speaker_vectors`` -- and the embedder
    it loads -- is skipped entirely when it is false, so an unconsented
    meeting never pays for an embedding pass it will not keep
    (``docs/modules/audio-speaker-identification.md``). ``_speaker_vectors``
    runs on the same waveform and renamed turns as everything downstream,
    before the block ends and the recording is deleted -- a vector not taken
    here can never be taken, because at confirmation time (#6) there is no
    audio left to take it from. The rows themselves are written *after*
    ``persist_transcript`` takes the meeting row lock (#184) in the
    transaction below, for the same reason that lock exists: two redelivered
    runs of the same job must not each see no rows and each insert their own.
    ``_store_speaker_embeddings`` re-checks the attestation at write time
    regardless -- that is the check that actually gates the write; the one
    here is only an early exit so an unconsented meeting never reaches the
    embedder.

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
        # Read once, early: an unconsented meeting must not pay for an
        # embedding pass it will not keep. `_store_speaker_embeddings`
        # re-checks this at write time regardless -- that is the check that
        # actually gates the write.
        consented = session.get(AudConsentAttestation, claim.meeting_id) is not None
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
            named = rename_speakers(get_diarizer().diarize(waveform))
            observations = _speaker_vectors(waveform, named) if consented else []

        # Before the write, not after: a collapsed transcript is not a
        # transcript, and the recording is already gone so there is nothing to
        # re-run.
        detect_repetition(transcription).raise_if_collapsed()

        spoken = assign_speakers(transcription, named)
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
            # After persist_transcript, not before: that call is what takes
            # the meeting row lock (#184) that serialises two redelivered
            # runs of the same job. Writing this before the lock would let
            # two concurrent runs each see no existing rows under READ
            # COMMITTED and each insert their own -- doubled rows with
            # nothing to catch it, since (meeting_id, speaker_label) carries
            # no unique constraint.
            _store_speaker_embeddings(session, meeting_id=meeting_id, observations=observations)
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


def _speaker_vectors(
    waveform: Waveform, turns: tuple[Turn, ...]
) -> list[tuple[str, np.ndarray, str]]:
    """One vector per speaker who said enough, taken while the audio exists.

    The recording is deleted when the ``adopt`` block ends (invariant 11), so
    a vector not taken here can never be taken: at confirmation time (#6)
    there is no audio. ``turns`` are the renamed ``화자 N`` turns -- the same
    labels the transcript and ``participants`` carry -- so a stored
    observation can be looked up by the label shown on screen. Returns
    ``(speaker_label, vector, model_version)``; an embedder that cannot load
    or run returns an empty list and the meeting is transcribed as usual. The
    ``try`` around each speaker covers ``representative_waveform`` as well as
    ``embed`` -- a slicing failure for one speaker must not escape and fail
    the whole meeting any more than an embedding failure does; the transcript
    is the product, the vector is an extra.
    """
    settings = get_settings()
    embedder = Embedder(token=settings.hf_token)
    try:
        embedder.warm_up()
    except Exception as exc:
        log.warning("speaker_embedding_unavailable", error=type(exc).__name__)
        return []

    vectors: list[tuple[str, np.ndarray, str]] = []
    for label in dict.fromkeys(turn.speaker for turn in turns):
        try:
            piece = representative_waveform(
                waveform,
                turns,
                label,
                max_seconds=settings.speaker_embedding_max_s,
                min_seconds=settings.speaker_embedding_min_s,
            )
            if piece is None:
                continue
            vectors.append((label, embedder.embed(piece), embedder.checkpoint))
        except Exception as exc:
            log.warning("speaker_embedding_failed", error=type(exc).__name__)
    return vectors


def _store_speaker_embeddings(
    session: Session,
    *,
    meeting_id: str,
    observations: list[tuple[str, np.ndarray, str]],
) -> None:
    """Write one observation row per speaker -- only for a meeting somebody
    has attested consent for.

    An embedding is biometric data. Without an attestation the transcript is
    still produced and nothing about anyone's voice is kept. The delete-then-
    insert makes a re-run replace a meeting's observations rather than
    doubling them, the same way ``persist_transcript`` replaces utterances --
    and, like that replace, it must run after ``persist_transcript`` has
    already taken the meeting row lock (#184): called before the lock, two
    redelivered runs of the same job would each see no existing rows under
    READ COMMITTED and each insert their own, and there is no unique
    constraint on ``(meeting_id, speaker_label)`` to catch it. The caller in
    ``process_recording`` is where that ordering is enforced; this function
    only assumes the lock is already held.

    The write itself is wrapped in its own guard: ``packages/core``'s engine
    does not set ``hide_parameters``, so SQLAlchemy puts bound parameters --
    here a 256-float vector -- into a raised ``StatementError``'s message
    (#356, fixed outside this branch). This module's write path must not make
    that worse, so a ``SQLAlchemyError`` here is caught and logged by
    exception type only, never its message.

    A SAVEPOINT (``begin_nested``), not a plain ``rollback()``: this call
    shares its session with ``persist_transcript`` and the rest of the
    transaction that follows it, and a full rollback would discard their work
    too, not just this one. A failed vector write must never fail a meeting
    whose transcript is otherwise fine.
    """
    if not observations:
        return
    if session.get(AudConsentAttestation, meeting_id) is None:
        log.info("speaker_embeddings_skipped_no_consent", meeting_id=meeting_id)
        return
    try:
        with session.begin_nested():
            session.execute(
                sa.delete(AudSpeakerEmbedding).where(AudSpeakerEmbedding.meeting_id == meeting_id)
            )
            for label, vector, model_version in observations:
                session.add(
                    AudSpeakerEmbedding(
                        meeting_id=meeting_id,
                        speaker_label=label,
                        vector=[float(x) for x in vector],
                        model_version=model_version,
                    )
                )
    except SQLAlchemyError as exc:
        log.warning("speaker_embeddings_failed", error=type(exc).__name__)
        return
    log.info("speaker_embeddings_stored", meeting_id=meeting_id, speakers=len(observations))


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
