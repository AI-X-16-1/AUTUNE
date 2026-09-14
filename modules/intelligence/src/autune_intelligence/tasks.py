"""Celery tasks for module E.

E aggregates B, C and D. Any of them can fail, so aggregation runs when all
three have reported or when the timeout elapses, and records which sources were
missing. It never blocks a user-visible result on a failed module.

The handlers parse a contract and delegate to ``service``; only the Celery
enqueue lives here, because ``service`` never imports a task. ``aggregate`` runs
``service.aggregate_meeting`` and publishes the resulting ``IntelligenceSnapshot``
on ``autune.intelligence.completed``; a source that arrives after the first pass
reopens the meeting and re-enqueues ``aggregate``.
"""

from __future__ import annotations

from datetime import date, timedelta

import sqlalchemy as sa
from celery import shared_task

from autune_contracts import (
    INTELLIGENCE_COMPLETED,
    ContextLinks,
    ExtractionResult,
    GapReport,
    validate_major_version,
)
from autune_core import Meeting, get_logger, load_integration, publish, session_scope
from autune_integrations import SlackClient

from . import service
from .config import get_settings
from .models import IntelCompletion

log = get_logger(__name__)


@shared_task(name="autune.intelligence.on_extraction_completed", acks_late=True)
def on_extraction_completed(payload: dict) -> None:
    result = ExtractionResult.model_validate(payload)
    validate_major_version(result)
    _record(result.meeting_id, "extraction", payload)


@shared_task(name="autune.intelligence.on_gap_completed", acks_late=True)
def on_gap_completed(payload: dict) -> None:
    report = GapReport.model_validate(payload)
    validate_major_version(report)
    _record(report.meeting_id, "gap", payload)


@shared_task(name="autune.intelligence.on_context_completed", acks_late=True)
def on_context_completed(payload: dict) -> None:
    links = ContextLinks.model_validate(payload)
    validate_major_version(links)
    _record(links.meeting_id, "context", payload)


def _record(meeting_id: str, source: str, payload: dict) -> None:
    """Record one source's completion and enqueue ``aggregate`` when it is due."""
    with session_scope() as session:
        first = service.record_completion(session, meeting_id, source, payload)
        row = session.get(IntelCompletion, meeting_id)
        if row is None:  # record_completion just upserted it; a miss means a torn write
            raise RuntimeError(f"intel_completion row missing right after upsert: {meeting_id}")
        already_aggregated = row.aggregated_at is not None
        ready = service.ready_to_aggregate(row)

    log.info(
        "intelligence_completion_recorded",
        meeting_id=meeting_id,
        source=source,
        first=first,
        ready=ready,
        already_aggregated=already_aggregated,
    )

    if already_aggregated:
        log.info("intelligence_late_completion", meeting_id=meeting_id, source=source)
        with session_scope() as session:
            service.reopen(session, meeting_id)
        # notify=False: the speaking ratio is A's data and a late B/C/D arrival
        # did not change it, so a re-aggregation must not re-send the DM.
        aggregate.apply_async((meeting_id,), {"notify": False})
        return
    if first:
        aggregate.apply_async((meeting_id,), countdown=get_settings().aggregate_timeout_seconds)
    if ready:
        aggregate.apply_async((meeting_id,))


@shared_task(name="autune.intelligence.aggregate", acks_late=True)
def aggregate(meeting_id: str, notify: bool = True) -> None:
    """Aggregate the meeting and publish the snapshot.

    Fires when B, C and D have all reported or the timeout countdown elapses,
    and again after ``reopen`` when a source arrives late. Idempotent: a
    no-op pass publishes nothing.

    ``notify`` is ``True`` on the first pass and ``False`` on a re-aggregation:
    the personal speaking-ratio DM goes out once, not again each time a late
    source reopens the meeting.
    """
    with session_scope() as session:
        snapshot = service.aggregate_meeting(session, meeting_id)

    if snapshot is None:
        log.info("intelligence_aggregate_skipped", meeting_id=meeting_id)
        return
    publish(INTELLIGENCE_COMPLETED, snapshot.model_dump(mode="json"))
    log.info(
        "intelligence_aggregate_published",
        meeting_id=meeting_id,
        grade=snapshot.quality_score.grade,
        missing_sources=snapshot.missing_sources,
    )
    if notify:
        send_personal_feedback.apply_async((meeting_id,))


@shared_task(name="autune.intelligence.send_personal_feedback", acks_late=True)
def send_personal_feedback(meeting_id: str) -> None:
    """DM each identified participant their own speaking ratio for this meeting.

    Fired once, after the first aggregation. The ratio is computed from A's
    utterances, delivered by direct message, and not stored. A team that has not
    connected Slack is skipped rather than failed. See
    docs/architecture/privacy.md section 3.
    """
    with session_scope() as session:
        team_id = session.scalar(sa.select(Meeting.team_id).where(Meeting.id == meeting_id))
        if team_id is None:
            log.info("intelligence_personal_feedback_meeting_gone", meeting_id=meeting_id)
            return
        config = load_integration(session, team_id, "slack")
        if config is None:
            log.info(
                "intelligence_personal_feedback_no_slack",
                meeting_id=meeting_id,
                team_id=team_id,
            )
            return
        service.send_personal_feedback(session, SlackClient(config.require_secret()), meeting_id)


@shared_task(name="autune.intelligence.generate_weekly_report", acks_late=True)
def generate_weekly_report(team_id: str, period_end: str | None = None) -> None:
    """Aggregate the trailing 7 days into one ``intel_reports`` row and post it
    to the team's Slack channel.

    ``period_end`` is an ISO date string (JSON-safe for a Celery arg), defaulting
    to today; ``period_start`` is 7 days before it. Unlike
    ``send_personal_feedback``, the report is generated and persisted whether or
    not Slack is connected — it is also served by ``GET /reports/{team_id}``, so
    a team without Slack still sees it on the dashboard. Only the channel post is
    skipped: without a connected Slack, or without a ``channel`` configured on
    it. There is no ``apps/worker`` beat schedule calling this yet — see
    docs/modules/intelligence.md step 6.
    """
    end = date.fromisoformat(period_end) if period_end is not None else date.today()
    start = end - timedelta(days=7)

    with session_scope() as session:
        report = service.generate_weekly_report(session, team_id, start, end)
        config = load_integration(session, team_id, "slack")

    if config is None:
        log.info("intelligence_weekly_report_no_slack", team_id=team_id, period_start=str(start))
        return
    channel = config.config.get("channel")
    if channel is None:
        log.info("intelligence_weekly_report_no_channel", team_id=team_id, period_start=str(start))
        return
    SlackClient(config.require_secret()).post_message(channel, report.body_markdown)
    log.info(
        "intelligence_weekly_report_sent",
        team_id=team_id,
        period_start=str(start),
        meeting_count=report.metrics_json["meeting_count"],
    )
