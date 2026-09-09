"""Celery tasks for module E.

E aggregates B, C and D. Any of them can fail, so aggregation runs when all
three have reported or when the timeout elapses, and records which sources were
missing. It never blocks a user-visible result on a failed module.

The handlers parse a contract and delegate to ``service``; only the Celery
enqueue lives here, because ``service`` never imports a task. Scoring,
classification, prediction and publishing ``IntelligenceSnapshot`` are P0.3 —
``aggregate`` here only closes the completion lifecycle.
"""

from __future__ import annotations

from celery import shared_task

from autune_contracts import ContextLinks, ExtractionResult, GapReport, validate_major_version
from autune_core import get_logger, session_scope

from . import service
from .config import get_settings
from .models import IntelCompletion

log = get_logger(__name__)


@shared_task(name="autune.intelligence.on_extraction_completed", acks_late=True)
def on_extraction_completed(payload: dict) -> None:
    result = ExtractionResult.model_validate(payload)
    validate_major_version(result)
    _record(result.meeting_id, "extraction")


@shared_task(name="autune.intelligence.on_gap_completed", acks_late=True)
def on_gap_completed(payload: dict) -> None:
    report = GapReport.model_validate(payload)
    validate_major_version(report)
    _record(report.meeting_id, "gap")


@shared_task(name="autune.intelligence.on_context_completed", acks_late=True)
def on_context_completed(payload: dict) -> None:
    links = ContextLinks.model_validate(payload)
    validate_major_version(links)
    _record(links.meeting_id, "context")


def _record(meeting_id: str, source: str) -> None:
    """Record one source's completion and enqueue ``aggregate`` when it is due."""
    with session_scope() as session:
        first = service.record_completion(session, meeting_id, source)
        row = session.get(IntelCompletion, meeting_id)
        assert row is not None  # record_completion just upserted it  # noqa: S101
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
        log.info(
            "intelligence_late_completion",
            meeting_id=meeting_id,
            source=source,
            note="re-aggregation warranted; deferred to P0.3",
        )
        return
    if first:
        aggregate.apply_async((meeting_id,), countdown=get_settings().aggregate_timeout_seconds)
    if ready:
        aggregate.apply_async((meeting_id,))


@shared_task(name="autune.intelligence.aggregate", acks_late=True)
def aggregate(meeting_id: str) -> None:
    """Close the completion lifecycle for a meeting.

    Fires when B, C and D have all reported or when the timeout countdown
    elapses. Idempotent: the countdown and an all-three trigger both enqueue it.
    """
    with session_scope() as session:
        missing = service.close_aggregation(session, meeting_id)

    if missing is None:
        log.info("intelligence_aggregate_skipped", meeting_id=meeting_id)
        return
    log.info("intelligence_aggregate_closed", meeting_id=meeting_id, missing_sources=missing)
    # TODO(이승환, P0.3): score, classify, predict; publish IntelligenceSnapshot
    #   with missing_sources filled in.
