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

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Final

import sqlalchemy as sa
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from autune_contracts import (
    ActionItem,
    ActionStatus,
    ContextLinks,
    ExtractionResult,
    GapReport,
    GapSeverity,
    IntelligenceSnapshot,
    Participation,
    QualityScore,
)
from autune_contracts.intelligence import Grade
from autune_core import Meeting

from .config import get_settings
from .models import IntelCompletion, IntelGapPattern, IntelScore

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
"""One decision per this many minutes scores decision_density 1.0.

What counts as "one decision" is B's grouping, not a fixed unit: several
utterances become one ``Decision`` only if they fall inside
``autune_extraction.decisions.DEFAULT_MAX_GAP``. That constant is unvalidated
(#53, ADR 0006), and because the density is capped at 1.0, over-splitting there
reads here as a *better* meeting. Retune this against the same evaluation set,
not independently.
"""
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

_MAX_PATTERN_TYPE: Final = 100
"""``Gap.category`` has no length limit but ``intel_gap_patterns.pattern_type`` is
``String(100)`` and part of the PK; truncate before it reaches the table."""


def _decision_density(decision_count: int, duration_minutes: float) -> float | None:
    """None when B reported no decisions — "not measured", not "scored zero".

    Until the extraction classifier (#10) ships, ``ExtractionResult.decisions``
    is always empty, so a ``0.0`` here would peg 0.3 of every meeting's score to
    zero. Returning ``None`` lets ``_quality_score`` renormalise the weight away.
    The cost: a meeting that genuinely ended with no decisions now scores the
    same as one we could not measure.
    """
    if decision_count == 0:
        return None
    expected = max(1.0, duration_minutes / DECISION_CADENCE_MINUTES)
    return min(1.0, decision_count / expected)


def _gap_burden(high_gap_count: int) -> float:
    return 1.0 - min(1.0, high_gap_count / HIGH_GAP_CEILING)


def _action_item_completion_rate(action_items: list[ActionItem]) -> float | None:
    if not action_items:
        return None
    done = sum(1 for a in action_items if a.status != ActionStatus.NEEDS_CONFIRMATION)
    return done / len(action_items)


def _participation_balance(participation: list[Participation]) -> float | None:
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


def reopen(session: Session, meeting_id: str) -> None:
    """Clear ``aggregated_at`` so a late source triggers a fresh aggregation."""
    row = session.get(IntelCompletion, meeting_id, with_for_update=True)
    if row is not None:
        row.aggregated_at = None


def aggregate_meeting(session: Session, meeting_id: str) -> IntelligenceSnapshot | None:
    """Turn the staged B/C/D payloads into an IntelligenceSnapshot and persist it.

    Returns ``None`` when there is no completion row or it is already aggregated —
    the countdown task and an all-three trigger both call this. A late source
    calls ``reopen`` first.
    """
    row = session.get(IntelCompletion, meeting_id, with_for_update=True)
    if row is None or row.aggregated_at is not None:
        return None

    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return None
    duration_minutes = (meeting.duration_seconds or 0.0) / 60.0

    extraction = (
        ExtractionResult.model_validate(row.extraction_payload)
        if row.extraction_payload is not None
        else None
    )
    gap = GapReport.model_validate(row.gap_payload) if row.gap_payload is not None else None
    _ = (
        ContextLinks.model_validate(row.context_payload)
        if row.context_payload is not None
        else None
    )
    missing = missing_sources(row)

    high_gap_count = (
        sum(1 for g in gap.gaps if g.severity == GapSeverity.HIGH) if gap is not None else None
    )
    components: dict[str, float | None] = {
        "decision_density": (
            _decision_density(len(extraction.decisions), duration_minutes)
            if extraction is not None
            else None
        ),
        "gap_burden": _gap_burden(high_gap_count) if high_gap_count is not None else None,
        "action_item_completion_rate": (
            _action_item_completion_rate(extraction.action_items)
            if extraction is not None
            else None
        ),
        "participation_balance": (
            _participation_balance(gap.participation) if gap is not None else None
        ),
    }
    score = _quality_score(components)

    distribution = (
        dict(Counter(g.category[:_MAX_PATTERN_TYPE] for g in gap.gaps)) if gap is not None else {}
    )

    session.execute(
        pg_insert(IntelScore)
        .values(
            meeting_id=meeting_id,
            team_id=meeting.team_id,
            grade=score.grade,
            value=score.value,
            decision_density=components["decision_density"],
            gap_count=high_gap_count,
            action_item_completion_rate=components["action_item_completion_rate"],
            participation_balance=components["participation_balance"],
            missing_sources=missing,
        )
        .on_conflict_do_update(
            index_elements=["meeting_id"],
            set_={
                "team_id": meeting.team_id,
                "grade": score.grade,
                "value": score.value,
                "decision_density": components["decision_density"],
                "gap_count": high_gap_count,
                "action_item_completion_rate": components["action_item_completion_rate"],
                "participation_balance": components["participation_balance"],
                "missing_sources": missing,
                "updated_at": func.now(),
            },
        )
    )

    session.execute(sa.delete(IntelGapPattern).where(IntelGapPattern.meeting_id == meeting_id))
    if gap is not None:
        ids_by_category: dict[str, list[str]] = {}
        for g in gap.gaps:
            category = g.category[:_MAX_PATTERN_TYPE]
            ids_by_category.setdefault(category, []).append(g.id)
        for category, count in distribution.items():
            session.add(
                IntelGapPattern(
                    meeting_id=meeting_id,
                    pattern_type=category,
                    team_id=meeting.team_id,
                    count=count,
                    source_gap_ids=ids_by_category[category],
                )
            )

    row.aggregated_at = datetime.now(UTC)
    session.flush()
    session.expire_all()

    return IntelligenceSnapshot(
        meeting_id=meeting_id,
        team_id=meeting.team_id,
        quality_score=score,
        gap_distribution=distribution,
        alignment=[],
        predictions=[],
        missing_sources=missing,
    )
