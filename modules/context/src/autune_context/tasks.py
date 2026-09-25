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

from datetime import UTC, datetime

import sqlalchemy as sa
from celery import shared_task
from sqlalchemy.orm import Session

import autune_context.pipeline  # noqa: F401  (registers the worker_process_init warm-up hook)
from autune_context import service
from autune_context.config import get_settings
from autune_context.models import CtxMeetingStatus
from autune_contracts import ExtractionResult, TranscriptReady, validate_major_version
from autune_core import IntegrationConfig, Meeting, get_logger, load_integration, session_scope
from autune_integrations import SlackClient

log = get_logger(__name__)


def _slack_target(
    session: Session, meeting_id: str, *, event_prefix: str
) -> tuple[str, IntegrationConfig] | None:
    """This meeting's team's Slack channel and credentials, or ``None`` with a
    logged reason -- shared by ``notify_context_events`` and
    ``notify_late_drift``, which otherwise repeat the same three checks."""
    team_id = session.scalar(sa.select(Meeting.team_id).where(Meeting.id == meeting_id))
    if team_id is None:
        log.info(f"{event_prefix}_meeting_gone", meeting_id=meeting_id)
        return None
    config = load_integration(session, team_id, "slack")
    if config is None:
        log.info(f"{event_prefix}_no_slack", meeting_id=meeting_id, team_id=team_id)
        return None
    channel = config.config.get("channel")
    if channel is None:
        log.info(f"{event_prefix}_no_channel", meeting_id=meeting_id, team_id=team_id)
        return None
    return channel, config


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
    can be published -- ``force=True`` when this lineage arrived *late* (the
    B-timeout fallback already published without it), so the republish isn't
    blocked by the ordinary ``published_at`` guard. See
    ``service.build_decision_lineage``'s ``was_late`` return and
    ``publish_if_ready(force=...)``.
    """
    result = ExtractionResult.model_validate(payload)
    validate_major_version(result)

    log.info(
        "context_received_extraction",
        meeting_id=result.meeting_id,
        decisions=len(result.decisions),
    )
    was_late = service.build_decision_lineage(result)
    publish_if_ready.delay(result.meeting_id, force=was_late)


@shared_task(name="autune.context.publish_if_ready", acks_late=True)
def publish_if_ready(meeting_id: str, force: bool = False) -> None:
    """Publish ContextLinks once both halves are in, or when B has timed out.

    Never block topic links on a failure in B: publish what exists and name what
    is missing.

    ``force`` is set only by ``on_extraction_completed`` for a lineage that
    arrived late; it republishes past the ordinary ``published_at`` guard and
    routes to ``notify_late_drift`` (drift only) instead of
    ``notify_context_events`` (topic links already went out the first time --
    see ``service.publish_if_ready``'s docstring).
    """
    published = service.publish_if_ready(meeting_id, force=force)
    log.info("context_publish_checked", meeting_id=meeting_id, published=published, force=force)
    if not published:
        return
    if force:
        notify_late_drift.apply_async((meeting_id,))
        return
    # ``service.publish_if_ready`` only returns True once per meeting
    # (guarded by ``published_at``), so this enqueues ``notify_context_events``
    # only once even though the task itself is armed twice (an immediate
    # check and a B-timeout fallback) and can also be retried by Celery.
    # That covers *enqueueing* once, not *executing* once -- ``acks_late``
    # is at-least-once delivery, so Celery can still redeliver the queued
    # notify task itself. ``notify_context_events`` closes that gap on its
    # own with a ``notified_at`` claim, so a redelivered execution is a
    # no-op rather than a duplicate post.
    notify_context_events.apply_async((meeting_id,))


@shared_task(name="autune.context.notify_context_events", acks_late=True)
def notify_context_events(meeting_id: str) -> None:
    """Post this meeting's topic-link notices and decision-drift warnings.

    Fired once, right after ``ContextLinks`` is published. A team that has not
    connected Slack, or has connected it without a channel, is skipped rather
    than failed -- same convention as ``autune_intelligence.tasks``.

    ``notified_at`` claims the meeting -- atomically, via ``with_for_update``,
    mirroring ``publish_if_ready``'s ``published_at`` guard -- *before*
    anything is sent, so a Celery redelivery of this task is a no-op instead of
    a duplicate post. Collecting the rows to send and claiming both happen
    inside one session that is closed *before* any Slack HTTP call, the same
    "gather in session, send after" shape ``autune_intelligence.tasks
    .generate_weekly_report`` uses -- a slow or rate-limited Slack response
    then never holds a pooled DB connection open.

    Skips drift specifically (not topic links) if ``late_drift_notified_at``
    is already set, or if a catch-up is owed (``late_drift_due_at`` is set):
    ordinarily this task claims and sends before this meeting's lineage
    exists at all, so ``collect_drift_notices`` is naturally empty here and
    ``notify_late_drift`` sends the drift later. But ``late_drift_due_at`` and
    the ``ctx_decision_versions`` rows it covers commit in the same
    transaction (see ``service.build_decision_lineage``), so if
    ``build_decision_lineage`` commits between this task's enqueue and its
    run, ``collect_drift_notices`` would otherwise find those rows too and
    send them here *and* again from ``notify_late_drift`` -- the
    ``late_drift_due_at`` check closes that race, not just the
    already-reversed one ``late_drift_notified_at`` alone covers.
    """
    with session_scope() as session:
        status = session.get(CtxMeetingStatus, meeting_id, with_for_update=True)
        if status is None:
            log.info("context_notify_meeting_gone", meeting_id=meeting_id)
            return
        if status.notified_at is not None:
            log.info("context_notify_already_claimed", meeting_id=meeting_id)
            return

        target = _slack_target(session, meeting_id, event_prefix="context_notify")
        if target is None:
            return
        channel, config = target

        topic_notices = service.collect_topic_link_notices(session, meeting_id)
        drift_notices = (
            []
            if status.late_drift_notified_at is not None or status.late_drift_due_at is not None
            else service.collect_drift_notices(session, meeting_id)
        )
        status.notified_at = datetime.now(tz=UTC)

    slack = SlackClient(config.require_secret())
    links_sent = service.send_topic_link_notices(slack, channel, topic_notices)
    drift_sent = service.send_decision_drift_notices(slack, channel, drift_notices)

    log.info(
        "context_notify_sent",
        meeting_id=meeting_id,
        topic_links=links_sent,
        drift_warnings=drift_sent,
    )


@shared_task(name="autune.context.notify_late_drift", acks_late=True)
def notify_late_drift(meeting_id: str) -> None:
    """Send this meeting's decision-drift warnings when its lineage finished
    late -- after ``ContextLinks`` had already published via the B-timeout
    fallback. See ``service.build_decision_lineage``'s ``was_late`` return and
    ``publish_if_ready(force=...)``, which routes here instead of
    ``notify_context_events``.

    Topic-link notices already went out in the original
    ``notify_context_events`` pass (topic linking finishes before the
    B-timeout fires); this meeting's own ``ctx_decision_versions`` did not
    exist at that first pass (B had not reported yet), so sending its drift
    here duplicates nothing from that earlier notify. Guarded by its own
    ``late_drift_notified_at`` claim -- the same shape as ``notified_at``,
    kept separate because it protects a different, later-firing send. Also
    clears ``late_drift_due_at`` -- see ``service.build_decision_lineage`` --
    since there is nothing left to catch up on once this claims.
    """
    with session_scope() as session:
        status = session.get(CtxMeetingStatus, meeting_id, with_for_update=True)
        if status is None:
            log.info("context_late_drift_meeting_gone", meeting_id=meeting_id)
            return
        if status.late_drift_notified_at is not None:
            log.info("context_late_drift_already_claimed", meeting_id=meeting_id)
            return

        target = _slack_target(session, meeting_id, event_prefix="context_late_drift")
        if target is None:
            # Nowhere to send, so nothing is owed. Left set, every later B
            # reprocess of this meeting would read "still owed" and force a
            # republish to E, and a team that connects Slack weeks from now
            # would get a drift notice for a meeting long past. Not marked
            # notified either -- nothing was sent.
            status.late_drift_due_at = None
            return
        channel, config = target

        drift_notices = service.collect_drift_notices(session, meeting_id)
        status.late_drift_notified_at = datetime.now(tz=UTC)
        status.late_drift_due_at = None

    slack = SlackClient(config.require_secret())
    drift_sent = service.send_decision_drift_notices(slack, channel, drift_notices)
    log.info("context_late_drift_sent", meeting_id=meeting_id, drift_warnings=drift_sent)
