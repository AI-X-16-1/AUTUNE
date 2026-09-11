"""Celery tasks for module B.

Discovered automatically by apps/worker. Tasks parse, delegate to ``service``,
and publish. Every task must be safe to run twice — see
docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from celery import shared_task

from autune_contracts import TranscriptReady, validate_major_version
from autune_core import get_logger, session_scope

from . import service
from .pipeline.registry import get_classifier

log = get_logger(__name__)


@shared_task(name="autune.extraction.on_transcript_ready", acks_late=True)
def on_transcript_ready(payload: dict) -> None:
    """Consume TranscriptReady from module A: classify, then group decisions.

    ``shared_task`` binds to whichever Celery app is running, so this module
    never imports apps/worker.

    Classification happens between two sessions, never inside one -- it is
    minutes of inference, and a transaction held around it holds a connection
    and its locks for all of them (``service.classify_utterances``). The first
    session only reads which utterances belong to a speaker who consented
    (privacy.md section 5); nobody else's speech reaches the classifier. The two
    writes that follow share one transaction: classifications and decisions come
    from the same predictions, and a meeting holding one run's labels and
    another run's decisions is not a state anything downstream should be able to
    read.

    Safe to run twice. Both writes replace the meeting's rows rather than add to
    them, so a redelivered task ends where the first one did.
    """
    transcript = TranscriptReady.model_validate(payload)
    validate_major_version(transcript)
    # Refuses a transcript whose raw audio was not deleted or text not masked.
    transcript.require_privacy_guarantees()

    log.info(
        "extraction_received_transcript",
        meeting_id=transcript.meeting_id,
        utterances=len(transcript.utterances),
    )

    with session_scope() as session:
        consented = service.consented_utterance_ids(session, transcript.meeting_id)

    classifier = get_classifier()
    classified = service.classify_utterances(classifier, transcript.utterances, consented=consented)

    with session_scope() as session:
        stored = service.store_classifications(
            session,
            meeting_id=transcript.meeting_id,
            utterances=classified,
            model_version=classifier.model_version,
        )
        decisions = service.build_decisions(
            session, meeting_id=transcript.meeting_id, utterances=classified
        )

    # Counts and ids only. The utterances are meeting content.
    log.info(
        "extraction_classified",
        meeting_id=transcript.meeting_id,
        utterances=len(classified),
        excluded=sum(1 for u in transcript.utterances if u.id not in consented),
        classified=stored,
        decisions=len(decisions),
        model_version=classifier.model_version,
    )
    # TODO(강민구): step 3, action items from commitments (#11); steps 4 and 6,
    # NLI and the confirmation DM for ambiguous agreement (#12); step 8, publish
    # ExtractionResult with ``autune_core.publish`` (landed in #145; wiring #31).
    # ``build_decisions`` gives fresh dec_ ids on every run, so every run has to
    # publish -- module D's lineage points at the old ids otherwise.
