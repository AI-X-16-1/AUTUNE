"""Module E completion tracking against real PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from autune_intelligence import service
from autune_intelligence.models import IntelCompletion


def test_first_source_creates_the_row_and_reports_first(db_session: Session, meeting: str) -> None:
    first = service.record_completion(db_session, meeting, "extraction")
    db_session.flush()

    assert first is True
    row = db_session.get(IntelCompletion, meeting)
    assert row is not None
    assert row.extraction_at is not None
    assert row.first_seen_at is not None
    assert row.gap_at is None and row.context_at is None
    assert row.aggregated_at is None


def test_second_source_is_not_first_and_keeps_the_earlier_timestamps(
    db_session: Session, meeting: str
) -> None:
    service.record_completion(db_session, meeting, "extraction")
    db_session.flush()
    before = db_session.get(IntelCompletion, meeting)
    first_seen, extraction_at = before.first_seen_at, before.extraction_at

    second = service.record_completion(db_session, meeting, "gap")
    db_session.flush()

    assert second is False
    row = db_session.get(IntelCompletion, meeting)
    assert row.first_seen_at == first_seen
    assert row.extraction_at == extraction_at
    assert row.gap_at is not None


def test_recording_the_same_source_twice_is_idempotent(db_session: Session, meeting: str) -> None:
    service.record_completion(db_session, meeting, "gap")
    db_session.flush()
    gap_at = db_session.get(IntelCompletion, meeting).gap_at

    again = service.record_completion(db_session, meeting, "gap")
    db_session.flush()

    assert again is False
    assert db_session.get(IntelCompletion, meeting).gap_at == gap_at


def test_all_three_recorded_is_ready_with_nothing_missing(
    db_session: Session, meeting: str
) -> None:
    for source in ("extraction", "gap", "context"):
        service.record_completion(db_session, meeting, source)
    db_session.flush()

    row = db_session.get(IntelCompletion, meeting)
    assert service.ready_to_aggregate(row) is True
    assert service.missing_sources(row) == []


def test_partial_is_ready_once_the_timeout_has_passed(db_session: Session, meeting: str) -> None:
    service.record_completion(db_session, meeting, "extraction")
    db_session.flush()
    row = db_session.get(IntelCompletion, meeting)

    assert service.ready_to_aggregate(row, now=row.first_seen_at + timedelta(seconds=1)) is False
    assert service.ready_to_aggregate(row, now=row.first_seen_at + timedelta(hours=1)) is True


def test_close_aggregation_stamps_aggregated_at_and_returns_missing(
    db_session: Session, meeting: str
) -> None:
    service.record_completion(db_session, meeting, "extraction")
    service.record_completion(db_session, meeting, "gap")
    db_session.flush()

    missing = service.close_aggregation(db_session, meeting)
    db_session.flush()

    assert missing == ["context"]
    assert db_session.get(IntelCompletion, meeting).aggregated_at is not None


def test_close_aggregation_is_idempotent(db_session: Session, meeting: str) -> None:
    service.record_completion(db_session, meeting, "extraction")
    db_session.flush()

    assert service.close_aggregation(db_session, meeting) == ["gap", "context"]
    db_session.flush()
    assert service.close_aggregation(db_session, meeting) is None


def test_close_aggregation_with_no_completion_row_is_a_noop(
    db_session: Session, meeting: str
) -> None:
    assert service.close_aggregation(db_session, meeting) is None


def test_recording_a_late_source_after_close_does_not_reopen(
    db_session: Session, meeting: str
) -> None:
    service.record_completion(db_session, meeting, "extraction")
    service.record_completion(db_session, meeting, "gap")
    db_session.flush()
    service.close_aggregation(db_session, meeting)
    db_session.flush()
    aggregated_at = db_session.get(IntelCompletion, meeting).aggregated_at

    late = service.record_completion(db_session, meeting, "context")
    db_session.flush()

    assert late is False
    row = db_session.get(IntelCompletion, meeting)
    assert row.context_at is not None
    assert row.aggregated_at == aggregated_at
    assert isinstance(aggregated_at, datetime)
