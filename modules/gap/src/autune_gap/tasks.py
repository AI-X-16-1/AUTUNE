"""Celery tasks for module C.

Discovered automatically by apps/worker. Tasks parse, delegate to ``service``,
and publish. Every task must be safe to run twice — see
docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from celery import shared_task

from autune_contracts import TranscriptReady, validate_major_version
from autune_core import get_logger
from autune_gap import service
from autune_gap.enqueue import PUBLISH_REPORT

log = get_logger(__name__)


@shared_task(name="autune.gap.on_transcript_ready", acks_late=True)
def on_transcript_ready(payload: dict) -> None:
    """Consume TranscriptReady from module A.

    ``shared_task`` binds to whichever Celery app is running, so this module
    never imports apps/worker.
    """
    transcript = TranscriptReady.model_validate(payload)
    validate_major_version(transcript)
    # Refuses a transcript whose raw audio was not deleted or text not masked.
    transcript.require_privacy_guarantees()

    log.info(
        "gap_received_transcript",
        meeting_id=transcript.meeting_id,
        utterances=len(transcript.utterances),
    )
    service.build_topic_graph(transcript)
    service.detect_gaps(transcript.meeting_id)
    service.publish_report(transcript.meeting_id)


@shared_task(name=PUBLISH_REPORT, acks_late=True)
def publish_report(meeting_id: str) -> None:
    """Send E the report as S20 left it, after a template switch or a
    dismissal (#316, #471). Queued by ``router``; see
    ``service.republish_report``.
    """
    service.republish_report(meeting_id)
