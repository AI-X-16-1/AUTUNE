"""Module E completion tracking against real PostgreSQL."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from autune_contracts import ContextLinks, ExtractionResult, GapReport
from autune_intelligence import service
from autune_intelligence.models import IntelCompletion

_PAYLOAD = {
    "extraction": lambda mid: ExtractionResult(meeting_id=mid).model_dump(mode="json"),
    "gap": lambda mid: GapReport(meeting_id=mid).model_dump(mode="json"),
    "context": lambda mid: ContextLinks(meeting_id=mid).model_dump(mode="json"),
}


def _record(session: Session, meeting_id: str, source: str) -> bool:
    return service.record_completion(session, meeting_id, source, _PAYLOAD[source](meeting_id))


def test_first_source_creates_the_row_and_reports_first(db_session: Session, meeting: str) -> None:
    first = _record(db_session, meeting, "extraction")
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
    _record(db_session, meeting, "extraction")
    db_session.flush()
    before = db_session.get(IntelCompletion, meeting)
    first_seen, extraction_at = before.first_seen_at, before.extraction_at

    second = _record(db_session, meeting, "gap")
    db_session.flush()

    assert second is False
    row = db_session.get(IntelCompletion, meeting)
    assert row.first_seen_at == first_seen
    assert row.extraction_at == extraction_at
    assert row.gap_at is not None


def test_recording_the_same_source_twice_is_idempotent(db_session: Session, meeting: str) -> None:
    _record(db_session, meeting, "gap")
    db_session.flush()
    gap_at = db_session.get(IntelCompletion, meeting).gap_at

    again = _record(db_session, meeting, "gap")
    db_session.flush()

    assert again is False
    assert db_session.get(IntelCompletion, meeting).gap_at == gap_at


def test_all_three_recorded_is_ready_with_nothing_missing(
    db_session: Session, meeting: str
) -> None:
    for source in ("extraction", "gap", "context"):
        _record(db_session, meeting, source)
    db_session.flush()

    row = db_session.get(IntelCompletion, meeting)
    assert service.ready_to_aggregate(row) is True
    assert service.missing_sources(row) == []


def test_partial_is_ready_once_the_timeout_has_passed(db_session: Session, meeting: str) -> None:
    _record(db_session, meeting, "extraction")
    db_session.flush()
    row = db_session.get(IntelCompletion, meeting)

    assert service.ready_to_aggregate(row, now=row.first_seen_at + timedelta(seconds=1)) is False
    assert service.ready_to_aggregate(row, now=row.first_seen_at + timedelta(hours=1)) is True


def test_the_payload_is_stored_and_the_latest_one_wins(db_session: Session, meeting: str) -> None:
    from autune_contracts import GapReport

    thin = GapReport(meeting_id=meeting).model_dump(mode="json")
    service.record_completion(db_session, meeting, "gap", thin)
    db_session.flush()
    assert db_session.get(IntelCompletion, meeting).gap_payload == thin

    richer = GapReport(
        meeting_id=meeting,
        gaps=[
            {
                "id": "gap_1",
                "category": "schedule",
                "title": "no date",
                "severity": "high",
                "risk_score": 0.9,
            }
        ],
    ).model_dump(mode="json")
    service.record_completion(db_session, meeting, "gap", richer)
    db_session.flush()
    assert db_session.get(IntelCompletion, meeting).gap_payload == richer
