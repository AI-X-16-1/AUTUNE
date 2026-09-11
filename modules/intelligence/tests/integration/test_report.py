"""service.generate_weekly_report against real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_intelligence import service
from autune_intelligence.models import IntelGapPattern, IntelReport, IntelScore


def _meeting(session: Session, team_id: str) -> str:
    from autune_core import Meeting

    row = Meeting(team_id=team_id, title="m")
    session.add(row)
    session.flush()
    return row.id


def _score(session: Session, meeting_id: str, team_id: str, **kw: object) -> None:
    session.add(
        IntelScore(
            meeting_id=meeting_id,
            team_id=team_id,
            grade=kw.pop("grade", "B"),
            value=kw.pop("value", 0.72),
            **kw,
        )
    )


PERIOD_START = date(2026, 9, 7)
PERIOD_END = date(2026, 9, 14)


def test_generates_a_report_from_scores_and_gap_patterns_in_the_period(
    db_session: Session, team: str
) -> None:
    m1 = _meeting(db_session, team)
    m2 = _meeting(db_session, team)
    mid_week = datetime(2026, 9, 10, tzinfo=UTC)
    _score(db_session, m1, team, grade="A", value=0.9, created_at=mid_week)
    _score(db_session, m2, team, grade="C", value=0.6, created_at=mid_week)
    db_session.add(IntelGapPattern(meeting_id=m1, pattern_type="ownership", team_id=team, count=2))
    db_session.add(IntelGapPattern(meeting_id=m2, pattern_type="ownership", team_id=team, count=1))
    db_session.flush()

    report = service.generate_weekly_report(db_session, team, PERIOD_START, PERIOD_END)

    assert report.metrics_json["meeting_count"] == 2
    assert report.metrics_json["average_score"] == pytest.approx(0.75)
    assert report.metrics_json["grade_distribution"] == {"A": 1, "C": 1}
    assert report.metrics_json["gap_distribution"] == {"ownership": 3}
    assert sorted(report.source_meeting_ids) == sorted([m1, m2])
    assert "2건" in report.body_markdown


def test_excludes_scores_outside_the_period(db_session: Session, team: str) -> None:
    inside = _meeting(db_session, team)
    before = _meeting(db_session, team)
    after = _meeting(db_session, team)
    _score(db_session, inside, team, created_at=datetime(2026, 9, 10, tzinfo=UTC))
    _score(db_session, before, team, created_at=datetime(2026, 9, 6, 23, 59, tzinfo=UTC))
    _score(db_session, after, team, created_at=datetime(2026, 9, 14, 0, 0, tzinfo=UTC))
    db_session.flush()

    report = service.generate_weekly_report(db_session, team, PERIOD_START, PERIOD_END)

    assert report.source_meeting_ids == [inside]


def test_a_period_with_no_scored_meetings_still_writes_a_row(
    db_session: Session, team: str
) -> None:
    report = service.generate_weekly_report(db_session, team, PERIOD_START, PERIOD_END)

    assert report.metrics_json["meeting_count"] == 0
    assert report.source_meeting_ids == []
    assert "분석된 회의가 없습니다" in report.body_markdown


def test_calling_twice_updates_the_same_row_instead_of_duplicating(
    db_session: Session, team: str
) -> None:
    m1 = _meeting(db_session, team)
    _score(db_session, m1, team, created_at=datetime(2026, 9, 10, tzinfo=UTC))
    db_session.flush()
    service.generate_weekly_report(db_session, team, PERIOD_START, PERIOD_END)

    m2 = _meeting(db_session, team)
    _score(db_session, m2, team, created_at=datetime(2026, 9, 11, tzinfo=UTC))
    db_session.flush()
    service.generate_weekly_report(db_session, team, PERIOD_START, PERIOD_END)

    rows = list(
        db_session.execute(
            sa.select(IntelReport).where(
                IntelReport.team_id == team, IntelReport.period_start == PERIOD_START
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].metrics_json["meeting_count"] == 2
