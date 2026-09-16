"""Celery tasks for the Meeting Context Engine.

Two entry points with different inputs:

- **Topic linking** needs only the transcript, so it runs in parallel with B
  and C, straight off ``autune.transcript.ready``.
- **Decision lineage** needs the decisions B extracted, so it runs after
  ``autune.extraction.completed``.

``ContextLinks`` is published once both have run — or, if B never reports, with
an empty ``decision_lineage`` and ``"extraction"`` in ``missing_sources``. A
failure in B must not cost the user their topic links.

A successful publish also fires ``notify_context_events``: the topic-link
notice and the decision-drift warning, docs/modules/context.md "Slack surface".

See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

import sqlalchemy as sa
from celery import shared_task

import autune_context.pipeline  # noqa: F401  (registers the worker_process_init warm-up hook)
from autune_context import service
from autune_context.config import get_settings
from autune_contracts import ExtractionResult, TranscriptReady, validate_major_version
from autune_core import Meeting, get_logger, load_integration, session_scope
from autune_integrations import SlackClient

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

    Matches each decision to a thread, runs NLI against the previous statement,
    and records how the decision moved. Then re-checks whether ``ContextLinks``
    can be published.
    """
    result = ExtractionResult.model_validate(payload)
    validate_major_version(result)

    log.info(
        "context_received_extraction",
        meeting_id=result.meeting_id,
        decisions=len(result.decisions),
    )
    service.build_decision_lineage(result)
    publish_if_ready.delay(result.meeting_id)


@shared_task(name="autune.context.publish_if_ready", acks_late=True)
def publish_if_ready(meeting_id: str) -> None:
    """Publish ContextLinks once both halves are in, or when B has timed out.

    Never block topic links on a failure in B: publish what exists and name what
    is missing.
    """
    published = service.publish_if_ready(meeting_id)
    log.info("context_publish_checked", meeting_id=meeting_id, published=published)
    if published:
        # ``service.publish_if_ready`` only returns True once per meeting
        # (guarded by ``published_at``), so this fires exactly once even
        # though the task itself is armed twice (an immediate check and a
        # B-timeout fallback) and can also be retried by Celery.
        notify_context_events.apply_async((meeting_id,))


@shared_task(name="autune.context.notify_context_events", acks_late=True)
def notify_context_events(meeting_id: str) -> None:
    """Post this meeting's topic-link notices and decision-drift warnings.

    Fired once, right after ``ContextLinks`` is published. A team that has not
    connected Slack, or has connected it without a channel, is skipped rather
    than failed -- same convention as ``autune_intelligence.tasks``.
    """
    with session_scope() as session:
        team_id = session.scalar(sa.select(Meeting.team_id).where(Meeting.id == meeting_id))
        if team_id is None:
            log.info("context_notify_meeting_gone", meeting_id=meeting_id)
            return
        config = load_integration(session, team_id, "slack")
        if config is None:
            log.info("context_notify_no_slack", meeting_id=meeting_id, team_id=team_id)
            return
        channel = config.config.get("channel")
        if channel is None:
            log.info("context_notify_no_channel", meeting_id=meeting_id, team_id=team_id)
            return

        slack = SlackClient(config.require_secret())
        links_sent = service.notify_topic_links(session, slack, channel, meeting_id)
        drift_sent = service.notify_decision_drift(session, slack, channel, meeting_id)

    log.info(
        "context_notify_sent",
        meeting_id=meeting_id,
        topic_links=links_sent,
        drift_warnings=drift_sent,
    )
