"""Celery tasks for module E.

E aggregates B, C and D. Any of them can fail, so aggregation runs when all
three have reported or when the timeout elapses, and records which sources were
missing. It never blocks a user-visible result on a failed module.
"""

from __future__ import annotations

from celery import shared_task

from autune_contracts import ContextLinks, ExtractionResult, GapReport, validate_major_version
from autune_core import get_logger

log = get_logger(__name__)


@shared_task(name="autune.intelligence.on_extraction_completed", acks_late=True)
def on_extraction_completed(payload: dict) -> None:
    result = ExtractionResult.model_validate(payload)
    validate_major_version(result)
    log.info("intelligence_received_extraction", meeting_id=result.meeting_id)
    # TODO(이승환): record completion, then aggregate when B, C and D are in.


@shared_task(name="autune.intelligence.on_gap_completed", acks_late=True)
def on_gap_completed(payload: dict) -> None:
    report = GapReport.model_validate(payload)
    validate_major_version(report)
    log.info("intelligence_received_gap", meeting_id=report.meeting_id)


@shared_task(name="autune.intelligence.on_context_completed", acks_late=True)
def on_context_completed(payload: dict) -> None:
    links = ContextLinks.model_validate(payload)
    validate_major_version(links)
    log.info("intelligence_received_context", meeting_id=links.meeting_id)


@shared_task(name="autune.intelligence.aggregate", acks_late=True)
def aggregate(meeting_id: str) -> None:
    """Build an IntelligenceSnapshot from whatever has arrived."""
    log.info("intelligence_aggregate_started", meeting_id=meeting_id)
    # TODO(이승환): score, classify, predict; set missing_sources for whatever
    #   had not reported; publish IntelligenceSnapshot.
