"""Module E aggregation against real PostgreSQL."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts import ContextLinks, ExtractionResult, GapReport, IntelligenceSnapshot
from autune_intelligence import service
from autune_intelligence.models import IntelCompletion, IntelGapPattern, IntelScore


def _stage(session: Session, meeting_id: str, source: str, payload: dict) -> None:
    service.record_completion(session, meeting_id, source, payload)
    session.flush()


def _extraction(meeting_id: str, decisions: int = 3, items: int = 2) -> dict:
    return ExtractionResult(
        meeting_id=meeting_id,
        decisions=[
            {"id": f"dec_{i}", "statement": "s", "confidence": 0.9} for i in range(decisions)
        ],
        action_items=[
            {"id": f"act_{i}", "description": "d", "status": "todo", "confidence": 0.9}
            for i in range(items)
        ],
    ).model_dump(mode="json")


def _gap(meeting_id: str) -> dict:
    return GapReport(
        meeting_id=meeting_id,
        gaps=[
            {
                "id": "gap_1",
                "category": "schedule",
                "title": "t",
                "severity": "high",
                "risk_score": 0.9,
            },
            {
                "id": "gap_2",
                "category": "schedule",
                "title": "t",
                "severity": "low",
                "risk_score": 0.2,
            },
            {
                "id": "gap_3",
                "category": "ownership",
                "title": "t",
                "severity": "medium",
                "risk_score": 0.6,
            },
        ],
        participation=[{"topic_id": "t1", "spoke": ["u1", "u2"], "silent": ["u3"]}],
    ).model_dump(mode="json")


def _context(meeting_id: str) -> dict:
    return ContextLinks(meeting_id=meeting_id).model_dump(mode="json")


def test_full_pass_writes_score_and_gap_patterns_and_returns_a_snapshot(
    db_session: Session, meeting: str, team: str
) -> None:
    _stage(db_session, meeting, "extraction", _extraction(meeting))
    _stage(db_session, meeting, "gap", _gap(meeting))
    _stage(db_session, meeting, "context", _context(meeting))

    snapshot = service.aggregate_meeting(db_session, meeting)
    db_session.flush()

    assert isinstance(snapshot, IntelligenceSnapshot)
    assert snapshot.team_id == team
    assert snapshot.missing_sources == []
    assert snapshot.gap_distribution == {"schedule": 2, "ownership": 1}
    assert snapshot.alignment == [] and snapshot.predictions == []

    score = db_session.get(IntelScore, meeting)
    assert score is not None
    assert score.grade == snapshot.quality_score.grade
    assert score.gap_count == 1  # one HIGH-severity gap
    assert score.team_id == team

    patterns = (
        db_session.execute(sa.select(IntelGapPattern).where(IntelGapPattern.meeting_id == meeting))
        .scalars()
        .all()
    )
    assert {p.pattern_type: p.count for p in patterns} == {"schedule": 2, "ownership": 1}
    schedule = next(p for p in patterns if p.pattern_type == "schedule")
    assert set(schedule.source_gap_ids) == {"gap_1", "gap_2"}

    assert db_session.get(IntelCompletion, meeting).aggregated_at is not None


def test_partial_pass_still_scores_and_names_the_missing_source(
    db_session: Session, meeting: str
) -> None:
    _stage(db_session, meeting, "extraction", _extraction(meeting))
    _stage(db_session, meeting, "gap", _gap(meeting))
    # context never arrives; force the timeout branch by ageing first_seen_at
    row = db_session.get(IntelCompletion, meeting)
    row.first_seen_at = row.first_seen_at.replace(year=2020)
    db_session.flush()

    snapshot = service.aggregate_meeting(db_session, meeting)

    assert snapshot is not None
    assert snapshot.missing_sources == ["context"]
    assert db_session.get(IntelScore, meeting).grade == snapshot.quality_score.grade


def test_only_context_present_scores_neutral(db_session: Session, meeting: str) -> None:
    _stage(db_session, meeting, "context", _context(meeting))
    row = db_session.get(IntelCompletion, meeting)
    row.first_seen_at = row.first_seen_at.replace(year=2020)
    db_session.flush()

    snapshot = service.aggregate_meeting(db_session, meeting)

    assert snapshot is not None
    assert snapshot.quality_score.value == 0.5
    assert snapshot.quality_score.grade == "E"
    assert snapshot.gap_distribution == {}
    assert snapshot.missing_sources == ["extraction", "gap"]


def test_re_aggregation_after_reopen_updates_the_score(db_session: Session, meeting: str) -> None:
    _stage(db_session, meeting, "extraction", _extraction(meeting, decisions=0, items=0))
    first = service.aggregate_meeting(db_session, meeting)
    db_session.flush()
    assert first is not None
    low = db_session.get(IntelScore, meeting).decision_density

    _stage(db_session, meeting, "extraction", _extraction(meeting, decisions=6, items=0))
    service.reopen(db_session, meeting)
    db_session.flush()
    second = service.aggregate_meeting(db_session, meeting)
    db_session.flush()

    assert second is not None
    assert db_session.get(IntelScore, meeting).decision_density > low


def test_aggregate_meeting_is_idempotent(db_session: Session, meeting: str) -> None:
    _stage(db_session, meeting, "extraction", _extraction(meeting))
    row = db_session.get(IntelCompletion, meeting)
    row.first_seen_at = row.first_seen_at.replace(year=2020)
    db_session.flush()

    assert service.aggregate_meeting(db_session, meeting) is not None
    db_session.flush()
    assert service.aggregate_meeting(db_session, meeting) is None


def test_aggregate_meeting_with_no_completion_row_returns_none(
    db_session: Session, meeting: str
) -> None:
    assert service.aggregate_meeting(db_session, meeting) is None
