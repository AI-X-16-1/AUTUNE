"""Module E completion logic — pure functions, no database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from autune_intelligence import service
from autune_intelligence.models import IntelCompletion

T0 = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _row(**arrivals: datetime) -> IntelCompletion:
    return IntelCompletion(meeting_id="mtg_1", first_seen_at=T0, **arrivals)


def test_sources_map_to_real_columns() -> None:
    columns = set(IntelCompletion.__table__.columns.keys())
    for source in service.SOURCES:
        assert f"{source}_at" in columns


def test_missing_sources_lists_the_absent_ones() -> None:
    assert service.missing_sources(_row(extraction_at=T0)) == ["gap", "context"]


def test_missing_sources_is_empty_when_all_present() -> None:
    assert service.missing_sources(_row(extraction_at=T0, gap_at=T0, context_at=T0)) == []


def test_ready_when_all_three_are_in() -> None:
    row = _row(extraction_at=T0, gap_at=T0, context_at=T0)
    assert service.ready_to_aggregate(row, now=T0) is True


def test_not_ready_while_partial_and_within_the_timeout() -> None:
    row = _row(extraction_at=T0)
    assert service.ready_to_aggregate(row, now=T0 + timedelta(seconds=1)) is False


def test_ready_when_partial_but_the_timeout_has_elapsed() -> None:
    row = _row(extraction_at=T0, gap_at=T0)
    assert service.ready_to_aggregate(row, now=T0 + timedelta(seconds=600)) is True


def test_unknown_source_is_rejected_before_any_database_use() -> None:
    with pytest.raises(ValueError, match="unknown source"):
        service.record_completion(None, "mtg_1", "audio")  # type: ignore[arg-type]
