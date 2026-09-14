"""Celery tasks for the Meeting Context Engine.

Two entry points with different inputs:

- **Topic linking** needs only the transcript, so it runs in parallel with B
  and C, straight off ``autune.transcript.ready``.
- **Decision lineage** needs the decisions B extracted, so it runs after
  ``autune.extraction.completed`` (Phase 3 fills in the lineage building).

``ContextLinks`` is published once both have run — or, if B never reports, with
an empty ``decision_lineage`` and ``"extraction"`` in ``missing_sources``. A
failure in B must not cost the user their topic links.

See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from celery import shared_task

import autune_context.pipeline  # noqa: F401  (registers the worker_process_init warm-up hook)
from autune_context import service
from autune_context.config import get_settings
from autune_contracts import ExtractionResult, TranscriptReady, validate_major_version
from autune_core import get_logger

log = get_logger(__name__)


@shared_task(name="autune.context.on_transcript_ready", acks_late=True)
def on_transcript_ready(payload: dict) -> None:
    """Link this meeting's topics to past meetings. Runs in parallel with B and C."""
    transcript = TranscriptReady.model_validate(payload)
    validate_major_version(transcript)
    # Refuses a transcript whose raw audio was not deleted or text not masked.
    transcript.require_privacy_guarantees()

    log.info(
        "context_received_transcript",
        meeting_id=transcript.meeting_id,
        utterances=len(transcript.utterances),
    )
    service.run_topic_linking(transcript)

    # Try now (B may already be in); also arm the B-timeout fallback.
    publish_if_ready.delay(transcript.meeting_id)
    publish_if_ready.apply_async(
        (transcript.meeting_id,), countdown=get_settings().publish_timeout_s
    )


@shared_task(name="autune.context.on_extraction_completed", acks_late=True)
def on_extraction_completed(payload: dict) -> None:
    """Thread B's decisions into lineage. Runs after B.

    Phase 2 only records that B reported; Phase 3 matches each decision to a
    thread and runs NLI against the previous statement.
    """
    result = ExtractionResult.model_validate(payload)
    validate_major_version(result)

    log.info(
        "context_received_extraction",
        meeting_id=result.meeting_id,
        decisions=len(result.decisions),
    )
    service.mark_extraction_seen(result.meeting_id)
    publish_if_ready.delay(result.meeting_id)


@shared_task(name="autune.context.publish_if_ready", acks_late=True)
def publish_if_ready(meeting_id: str) -> None:
    """Publish ContextLinks once both halves are in, or when B has timed out.

    Never block topic links on a failure in B: publish what exists and name what
    is missing.
    """
    published = service.publish_if_ready(meeting_id)
    log.info("context_publish_checked", meeting_id=meeting_id, published=published)
