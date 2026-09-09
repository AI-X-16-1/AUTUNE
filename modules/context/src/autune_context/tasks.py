"""Celery tasks for module D.

D's two jobs have different inputs, so they are two tasks:

- **Topic linking** needs only the transcript, so it runs in parallel with B
  and C, straight off ``autune.transcript.ready``.
- **Decision lineage** needs the decisions B extracted, so it runs after
  ``autune.extraction.completed``.

D publishes ``ContextLinks`` once both have run — or, if B never reports, with
an empty ``decision_lineage`` and ``"extraction"`` in ``missing_sources``. A
failure in B must not cost the user their topic links.

See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from celery import shared_task

# Importing ``pipeline`` registers the worker_process_init warm-up hook;
# importing ``service`` registers the meeting-deletion cleanup hook.
from autune_context import pipeline, service  # noqa: F401
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
    # TODO(문민재): embed topics into ctx_embeddings (pgvector), hybrid-retrieve
    #   past meetings, re-rank, persist ctx_topic_links, then try to publish.


@shared_task(name="autune.context.on_extraction_completed", acks_late=True)
def on_extraction_completed(payload: dict) -> None:
    """Thread B's decisions into lineage. Runs after B.

    ``result.decisions`` carries what B judged to be a decision in this meeting.
    D decides which existing thread each one belongs to — that matching is D's,
    and the thread id is D's own (``thr_``).
    """
    result = ExtractionResult.model_validate(payload)
    validate_major_version(result)

    log.info(
        "context_received_extraction",
        meeting_id=result.meeting_id,
        decisions=len(result.decisions),
    )
    # TODO(문민재): match each decision to a thread, run NLI against the previous
    #   statement, record the version, then try to publish.


@shared_task(name="autune.context.publish_if_ready", acks_late=True)
def publish_if_ready(meeting_id: str) -> None:
    """Publish ContextLinks once both halves are in, or when B has timed out.

    Never block topic links on a failure in B: publish what exists and name what
    is missing.
    """
    log.info("context_publish_checked", meeting_id=meeting_id)
    # TODO(문민재): if topic linking is done and either lineage is done or the
    #   timeout has passed, publish ContextLinks with missing_sources filled in.
