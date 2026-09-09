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

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .config import get_settings
from .models import IntelCompletion

SOURCES: tuple[str, ...] = ("extraction", "gap", "context")
"""The three upstream modules E waits on. Each maps to an ``<source>_at`` column
on ``intel_completion``."""

_COLUMN = {source: f"{source}_at" for source in SOURCES}


def record_completion(session: Session, meeting_id: str, source: str) -> bool:
    """Record that ``source`` has reported for ``meeting_id``.

    Upserts ``intel_completion``: sets ``<source>_at`` on the first arrival for
    that source and leaves it untouched on a re-delivery, and stamps
    ``first_seen_at`` once when the row is created.

    Returns ``True`` when this call created the row — i.e. this was the first of
    the three sources seen for the meeting, and the caller should schedule the
    timeout countdown.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")

    column = _COLUMN[source]
    now = datetime.now(UTC)
    stmt = (
        pg_insert(IntelCompletion)
        .values(meeting_id=meeting_id, first_seen_at=now, **{column: now})
        .on_conflict_do_update(
            index_elements=["meeting_id"],
            set_={column: func.coalesce(getattr(IntelCompletion, column), now)},
        )
        .returning(text("(xmax = 0) AS inserted"))
    )
    inserted = bool(session.execute(stmt).scalar_one())
    # The upsert is a Core statement, so the ORM identity map still holds the
    # pre-write row. Drop it so the caller's next read sees the new timestamps.
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
