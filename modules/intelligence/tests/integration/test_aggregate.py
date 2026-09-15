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
    """Three gaps. ``category`` is deliberately *not* one of the six pattern
    types — classification reads ``title``, not ``category`` (service.py) — and
    ``title`` carries the label ``FakeGapClassifier`` matches verbatim."""
    return GapReport(
        meeting_id=meeting_id,
        gaps=[
            {
                "id": "gap_1",
                "category": "unfiled_note",
                "title": "schedule slipping",
                "severity": "high",
                "risk_score": 0.9,
            },
            {
                "id": "gap_2",
                "category": "unfiled_note",
                "title": "schedule unclear",
                "severity": "low",
                "risk_score": 0.2,
            },
            {
                "id": "gap_3",
                "category": "unfiled_note",
                "title": "ownership unclear",
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
    assert schedule.classifier_version == "fake"
    assert schedule.avg_confidence == 1.0  # FakeGapClassifier: verbatim label match

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
    from autune_core import Meeting

    db_session.get(Meeting, meeting).duration_seconds = 3600  # 60 min: density scales below cap
    db_session.flush()

    _stage(db_session, meeting, "extraction", _extraction(meeting, decisions=1, items=0))
    first = service.aggregate_meeting(db_session, meeting)
    db_session.flush()
    assert first is not None
    low = db_session.get(IntelScore, meeting).decision_density
    assert low is not None

    _stage(db_session, meeting, "extraction", _extraction(meeting, decisions=6, items=0))
    service.reopen(db_session, meeting)
    db_session.flush()
    second = service.aggregate_meeting(db_session, meeting)
    db_session.flush()

    assert second is not None
    assert db_session.get(IntelScore, meeting).decision_density > low


def _gap_with_labels(meeting_id: str, pairs: list[tuple[str, str]]) -> dict:
    """A GapReport whose gaps are ``(gap_id, pattern_label)`` pairs, all low
    severity. ``category`` is a fixed, unrelated free-text value; the label is
    carried in ``title`` since that is what classification reads."""
    return GapReport(
        meeting_id=meeting_id,
        gaps=[
            {
                "id": gap_id,
                "category": "unfiled_note",
                "title": f"{label} unclear",
                "severity": "low",
                "risk_score": 0.2,
            }
            for gap_id, label in pairs
        ],
        participation=[{"topic_id": "t1", "spoke": ["u1", "u2"], "silent": ["u3"]}],
    ).model_dump(mode="json")


def test_re_aggregation_replaces_the_gap_patterns(db_session: Session, meeting: str) -> None:
    _stage(db_session, meeting, "extraction", _extraction(meeting))
    _stage(
        db_session,
        meeting,
        "gap",
        _gap_with_labels(meeting, [("gap_1", "schedule"), ("gap_2", "ownership")]),
    )
    _stage(db_session, meeting, "context", _context(meeting))
    assert service.aggregate_meeting(db_session, meeting) is not None
    db_session.flush()

    _stage(
        db_session,
        meeting,
        "gap",
        _gap_with_labels(meeting, [("gap_3", "schedule"), ("gap_4", "risk")]),
    )
    service.reopen(db_session, meeting)
    db_session.flush()
    assert service.aggregate_meeting(db_session, meeting) is not None
    db_session.flush()

    patterns = (
        db_session.execute(sa.select(IntelGapPattern).where(IntelGapPattern.meeting_id == meeting))
        .scalars()
        .all()
    )
    by_type = {p.pattern_type: p for p in patterns}
    assert set(by_type) == {"schedule", "risk"}
    assert by_type["schedule"].source_gap_ids == ["gap_3"]
    assert by_type["risk"].source_gap_ids == ["gap_4"]


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


def test_gap_classification_normalizes_a_freeform_category(
    db_session: Session, meeting: str
) -> None:
    """C's ``Gap.category`` is free text (see the contract fixture's
    ``"technical_spec"``) and is not even passed to the classifier (service.py
    reads only ``title``) — this exercises ``FakeGapClassifier``'s keyword
    fallback on the Korean title, with an arbitrary, non-canonical category
    alongside it to confirm the category plays no part."""
    gap_payload = GapReport(
        meeting_id=meeting,
        gaps=[
            {
                "id": "gap_1",
                "category": "technical_spec",
                "title": "성능 요구사항이 정의되지 않았습니다",
                "severity": "high",
                "risk_score": 0.8,
            }
        ],
        participation=[{"topic_id": "t1", "spoke": ["u1", "u2"], "silent": ["u3"]}],
    ).model_dump(mode="json")
    _stage(db_session, meeting, "gap", gap_payload)
    row = db_session.get(IntelCompletion, meeting)
    row.first_seen_at = row.first_seen_at.replace(year=2020)
    db_session.flush()

    snapshot = service.aggregate_meeting(db_session, meeting)

    assert snapshot is not None
    assert snapshot.gap_distribution == {"scope": 1}
