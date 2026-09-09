"""Business logic for module E: Meeting Intelligence.

Owner: 이승환. See docs/modules/intelligence.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``intel_*`` tables.
Never imports another module.

This file holds the completion-tracking logic. E aggregates B, C and D; any of
them can fail, so aggregation runs when all three have reported or when a
timeout elapses. The Celery glue that enqueues the aggregate task lives in
``tasks.py`` — this module never imports a task.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from autune_contracts import QualityScore
from autune_contracts.intelligence import Grade

from .config import get_settings
from .models import IntelCompletion

SOURCES: tuple[str, ...] = ("extraction", "gap", "context")
"""The three upstream modules E waits on. Each maps to an ``<source>_at`` column
on ``intel_completion``."""

_COLUMN = {source: f"{source}_at" for source in SOURCES}


def record_completion(session: Session, meeting_id: str, source: str, payload: dict) -> bool:
    """Record that ``source`` has reported for ``meeting_id`` and stash its payload.

    Upserts ``intel_completion``: ``<source>_at`` is set on the first arrival and
    kept on a re-delivery (the countdown anchor must not move); ``<source>_payload``
    takes the latest payload every call. ``first_seen_at`` is stamped once.

    Returns ``True`` when this call created the row — the first of the three
    sources for the meeting, whose caller schedules the timeout countdown.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")

    at_column = _COLUMN[source]
    payload_column = f"{source}_payload"
    now = datetime.now(UTC)
    stmt = (
        pg_insert(IntelCompletion)
        .values(
            meeting_id=meeting_id,
            first_seen_at=now,
            **{at_column: now, payload_column: payload},
        )
        .on_conflict_do_update(
            index_elements=["meeting_id"],
            set_={
                at_column: func.coalesce(getattr(IntelCompletion, at_column), now),
                payload_column: payload,
            },
        )
        .returning(text("(xmax = 0) AS inserted"))
    )
    inserted = bool(session.execute(stmt).scalar_one())
    session.expire_all()
    return inserted


def missing_sources(row: IntelCompletion) -> list[str]:
    """The sources with no arrival timestamp on this completion row, in order."""
    return [source for source in SOURCES if getattr(row, _COLUMN[source]) is None]


def ready_to_aggregate(row: IntelCompletion, *, now: datetime | None = None) -> bool:
    """True when all three sources are in, or the timeout since the first has elapsed."""
    if not missing_sources(row):
        return True
    now = now or datetime.now(UTC)
    timeout = timedelta(seconds=get_settings().aggregate_timeout_seconds)
    return now - row.first_seen_at >= timeout


DECISION_CADENCE_MINUTES: Final = 10.0
"""One decision per this many minutes scores decision_density 1.0."""
HIGH_GAP_CEILING: Final = 5
"""This many HIGH-severity gaps drives gap_burden to 0.0."""
WEIGHTS: Final = {
    "decision_density": 0.3,
    "gap_burden": 0.3,
    "action_item_completion_rate": 0.2,
    "participation_balance": 0.2,
}
GRADE_CUTOFFS: Final = ((0.9, "A"), (0.8, "B"), (0.7, "C"), (0.6, "D"), (0.5, "E"))
"""Descending; value below the last cutoff is F. First heuristic — P2 tunes these."""


def _decision_density(decision_count: int, duration_minutes: float) -> float:
    expected = max(1.0, duration_minutes / DECISION_CADENCE_MINUTES)
    return min(1.0, decision_count / expected)


def _gap_burden(high_gap_count: int) -> float:
    return 1.0 - min(1.0, high_gap_count / HIGH_GAP_CEILING)


def _action_item_completion_rate(action_items: list) -> float | None:
    if not action_items:
        return None
    from autune_contracts import ActionStatus

    done = sum(1 for a in action_items if a.status != ActionStatus.NEEDS_CONFIRMATION)
    return done / len(action_items)


def _participation_balance(participation: list) -> float | None:
    if not participation:
        return None
    ratios = [len(p.spoke) / max(1, len(p.spoke) + len(p.silent)) for p in participation]
    return sum(ratios) / len(ratios)


def _grade_for(value: float) -> Grade:
    for cutoff, grade in GRADE_CUTOFFS:
        if value >= cutoff:
            return grade  # type: ignore[return-value]
    return "F"


def _quality_score(components: dict[str, float | None]) -> QualityScore:
    present = {k: v for k, v in components.items() if v is not None}
    if not present:
        value = 0.5
    else:
        total_weight = sum(WEIGHTS[k] for k in present)
        value = sum(v * WEIGHTS[k] for k, v in present.items()) / total_weight
    return QualityScore(grade=_grade_for(value), value=value)


def close_aggregation(session: Session, meeting_id: str) -> list[str] | None:
    """Mark the meeting aggregated; return which sources were missing at that point.

    Returns ``None`` when there is nothing to do — no completion row, or it was
    already closed. Both the timeout countdown and an all-three trigger can fire
    for the same meeting, so this must be idempotent.
    """
    row = session.get(IntelCompletion, meeting_id, with_for_update=True)
    if row is None or row.aggregated_at is not None:
        return None
    row.aggregated_at = datetime.now(UTC)
    return missing_sources(row)
