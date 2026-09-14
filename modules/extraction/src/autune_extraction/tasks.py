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
    """Consume TranscriptReady from module A: classify, then write what it found.

    Decisions grouped, draft items for commitments, and a record of every
    ambiguous agreement -- not yet asked about, because nothing can send the DM
    (#70, #30).

    ``shared_task`` binds to whichever Celery app is running, so this module
    never imports apps/worker.

    Classification happens between two sessions, never inside one -- it is
    minutes of inference, and a transaction held around it holds a connection
    and its locks for all of them (``service.classify_utterances``). The first
    session only reads which utterances belong to a speaker who consented
    (privacy.md section 5); nobody else's speech reaches the classifier. The
    writes that follow share one transaction: classifications, decisions and
    draft items come from the same predictions, and a meeting holding one run's
    labels and another run's items is not a state anything downstream should be
    able to read.

    Safe to run twice. Every write replaces the meeting's model-made rows rather
    than adding to them, so a redelivered task ends where the first one did --
    except that draft items are left alone once a person has edited any
    (``service.build_action_items``).
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
        items = service.build_action_items(
            session,
            meeting_id=transcript.meeting_id,
            utterances=transcript.utterances,
            classified=classified,
        )
        ambiguous = service.record_ambiguous_agreements(
            session, meeting_id=transcript.meeting_id, classified=classified
        )

    # Counts and ids only. The utterances are meeting content.
    log.info(
        "extraction_classified",
        meeting_id=transcript.meeting_id,
        utterances=len(classified),
        excluded=sum(1 for u in transcript.utterances if u.id not in consented),
        classified=stored,
        decisions=len(decisions),
        action_items=len(items) if items is not None else "kept",
        ambiguous=ambiguous,
        model_version=classifier.model_version,
    )
    # TODO(강민구): step 4, NLI over commitments and ambiguous agreement (#12,
    # no model chosen yet). Step 6, the DM: for each of
    # ``service.unasked_confirmations``, resolve the speaker's Slack account
    # and call ``service.ask_for_confirmation`` -- blocked on an account mapping
    # (#70) and a team Slack client (#30). Step 7, Notion and Jira (#30). Step 8,
    # publish ExtractionResult with ``autune_core.publish`` (landed in #145;
    # wiring #31).
    # ``build_decisions`` gives fresh dec_ ids on every run, so every run has to
    # publish -- module D's lineage points at the old ids otherwise.
