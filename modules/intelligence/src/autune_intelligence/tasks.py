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

from celery import current_app, shared_task

from autune_contracts import (
    INTELLIGENCE_COMPLETED,
    ContextLinks,
    ExtractionResult,
    GapReport,
    validate_major_version,
)
from autune_core import get_logger, session_scope

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
        aggregate.apply_async((meeting_id,))
        return
    if first:
        aggregate.apply_async((meeting_id,), countdown=get_settings().aggregate_timeout_seconds)
    if ready:
        aggregate.apply_async((meeting_id,))


@shared_task(name="autune.intelligence.aggregate", acks_late=True)
def aggregate(meeting_id: str) -> None:
    """Aggregate the meeting and publish the snapshot.

    Fires when B, C and D have all reported or the timeout countdown elapses,
    and again after ``reopen`` when a source arrives late. Idempotent: a
    no-op pass publishes nothing.
    """
    with session_scope() as session:
        snapshot = service.aggregate_meeting(session, meeting_id)

    if snapshot is None:
        log.info("intelligence_aggregate_skipped", meeting_id=meeting_id)
        return
    current_app.send_task(INTELLIGENCE_COMPLETED, args=[snapshot.model_dump(mode="json")])
    log.info(
        "intelligence_aggregate_published",
        meeting_id=meeting_id,
        grade=snapshot.quality_score.grade,
        missing_sources=snapshot.missing_sources,
    )
