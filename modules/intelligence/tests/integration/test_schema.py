"""Module E schema behaviour against real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting
from autune_intelligence.models import (
    IntelAlignment,
    IntelCompletion,
    IntelGapPattern,
    IntelPrediction,
    IntelReport,
    IntelScore,
)


def test_score_row_round_trips(db_session: Session, meeting: str, team: str) -> None:
    db_session.add(IntelScore(meeting_id=meeting, team_id=team, grade="B", value=0.72, gap_count=3))
    db_session.flush()
    got = db_session.get(IntelScore, meeting)
    assert got is not None and got.grade == "B" and got.value == pytest.approx(0.72)
    assert got.missing_sources == []


def test_grade_outside_a_to_f_is_rejected(db_session: Session, meeting: str, team: str) -> None:
    db_session.add(IntelScore(meeting_id=meeting, team_id=team, grade="Z", value=0.5))
    with pytest.raises(sa.exc.IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_score_value_must_be_a_unit_interval(
    db_session: Session, meeting: str, team: str, bad: float
) -> None:
    db_session.add(IntelScore(meeting_id=meeting, team_id=team, grade="C", value=bad))
    with pytest.raises(sa.exc.IntegrityError):
        db_session.flush()


def test_alignment_score_must_be_a_unit_interval(
    db_session: Session, meeting: str, team: str
) -> None:
    db_session.add(
        IntelAlignment(meeting_id=meeting, role_a="Dev", role_b="PM", team_id=team, score=1.5)
    )
    with pytest.raises(sa.exc.IntegrityError):
        db_session.flush()


def test_re_aggregation_replaces_alignment_rows_rather_than_duplicating(
    db_session: Session, meeting: str, team: str
) -> None:
    stmt = sa.dialects.postgresql.insert(IntelAlignment.__table__)
    row = {
        "meeting_id": meeting,
        "role_a": "Dev",
        "role_b": "PM",
        "team_id": team,
        "score": 0.4,
    }
    db_session.execute(stmt.values(**row))
    upsert = stmt.values(**{**row, "score": 0.9}).on_conflict_do_update(
        index_elements=["meeting_id", "role_a", "role_b"], set_={"score": 0.9}
    )
    db_session.execute(upsert)
    db_session.flush()

    scores = (
        db_session.execute(
            sa.select(IntelAlignment.score).where(IntelAlignment.meeting_id == meeting)
        )
        .scalars()
        .all()
    )
    assert scores == [pytest.approx(0.9)]


def test_deleting_a_meeting_cascades_to_meeting_scoped_intel_rows(
    db_session: Session, meeting: str, team: str
) -> None:
    db_session.add_all(
        [
            IntelCompletion(meeting_id=meeting, first_seen_at=datetime.now(UTC)),
            IntelScore(meeting_id=meeting, team_id=team, grade="A", value=0.9),
            IntelAlignment(meeting_id=meeting, role_a="Dev", role_b="PM", team_id=team, score=0.6),
            IntelGapPattern(meeting_id=meeting, pattern_type="scope_creep", team_id=team, count=1),
            IntelPrediction(
                meeting_id=meeting,
                kind="misalignment_risk",
                horizon_days=7,
                team_id=team,
                probability=0.3,
            ),
        ]
    )
    db_session.flush()

    db_session.execute(sa.delete(Meeting).where(Meeting.id == meeting))
    db_session.flush()

    for model in (IntelCompletion, IntelScore):
        assert db_session.get(model, meeting) is None
    for table in (
        IntelAlignment.__table__,
        IntelGapPattern.__table__,
        IntelPrediction.__table__,
    ):
        remaining = db_session.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
        assert remaining == 0


def test_report_is_keyed_by_team_and_period(db_session: Session, team: str) -> None:
    db_session.add(
        IntelReport(
            team_id=team,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 7),
            body_markdown="# Weekly",
            metrics_json={"quality": 0.8},
            source_meeting_ids=["mtg_a", "mtg_b"],
        )
    )
    db_session.flush()
    got = db_session.get(IntelReport, (team, date(2026, 9, 1)))
    assert got is not None and got.source_meeting_ids == ["mtg_a", "mtg_b"]
