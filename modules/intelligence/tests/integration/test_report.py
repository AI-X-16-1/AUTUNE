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
    assert report.metrics_json["partial_meeting_count"] == 0
    assert sorted(report.source_meeting_ids) == sorted([m1, m2])
    assert "2건" in report.body_markdown


def test_counts_partially_analyzed_meetings_in_the_period(db_session: Session, team: str) -> None:
    complete = _meeting(db_session, team)
    partial = _meeting(db_session, team)
    mid_week = datetime(2026, 9, 10, tzinfo=UTC)
    _score(db_session, complete, team, created_at=mid_week, missing_sources=[])
    _score(db_session, partial, team, created_at=mid_week, missing_sources=["gap"])
    db_session.flush()

    report = service.generate_weekly_report(db_session, team, PERIOD_START, PERIOD_END)

    assert report.metrics_json["partial_meeting_count"] == 1
    assert "부분 분석 1건" in report.body_markdown


def test_excludes_scores_outside_the_period(db_session: Session, team: str) -> None:
    inside = _meeting(db_session, team)
    before = _meeting(db_session, team)
    after = _meeting(db_session, team)
    _score(db_session, inside, team, created_at=datetime(2026, 9, 10, tzinfo=UTC))
    # The week runs from midnight Korean time (#809 review): 9/6 23:59 KST is
    # before it, 9/14 00:00 KST after it.
    _score(db_session, before, team, created_at=datetime(2026, 9, 6, 14, 59, tzinfo=UTC))
    _score(db_session, after, team, created_at=datetime(2026, 9, 13, 15, 0, tzinfo=UTC))
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


# --- action-item completion from B's counts (#605 step 5) -------------------------


def _held(session: Session, team_id: str, days_ago: int) -> str:
    from datetime import timedelta

    from autune_core import Meeting

    row = Meeting(
        team_id=team_id, title="m", started_at=datetime.now(UTC) - timedelta(days=days_ago)
    )
    session.add(row)
    session.flush()
    return row.id


def _progress(
    session: Session, team_id: str, meetings: list[dict[str, object]], *, minutes_ago: int = 0
) -> None:
    from datetime import timedelta

    from autune_contracts import TeamActionProgress

    service.store_action_progress(
        session,
        TeamActionProgress(
            team_id=team_id,
            as_of=datetime.now(UTC) - timedelta(minutes=minutes_ago),
            meetings=meetings,
        ),
    )


def _this_week() -> tuple[date, date]:
    """The seven Korean days up to today, as a report's week runs."""
    from datetime import timedelta

    end = datetime.now(service._KST).date()
    return end - timedelta(days=7), end


def test_the_weeks_report_shows_completion_overdue_and_carried_over(
    db_session: Session, team: str
) -> None:
    """Completion as the dashboard counts it (최근 4주 회의), not the quality
    score's confirmation rate; carried over is what meetings before this week
    left undone."""
    start, end = _this_week()
    this_week, a, b = (_held(db_session, team, days) for days in (2, 10, 14))
    old = _held(db_session, team, 40)  # outside the rate's four weeks, inside B's window
    _score(
        db_session,
        this_week,
        team,
        action_item_completion_rate=0.9,
        created_at=datetime.combine(start, datetime.min.time(), tzinfo=UTC),
    )
    _progress(
        db_session,
        team,
        [
            {"meeting_id": this_week, "confirmed": 4, "done": 1, "overdue": 0},
            {"meeting_id": a, "confirmed": 4, "done": 2, "overdue": 1},
            {"meeting_id": b, "confirmed": 2, "done": 1, "overdue": 0},
            {"meeting_id": old, "confirmed": 2, "done": 0, "overdue": 1},
        ],
    )

    report = service.generate_weekly_report(db_session, team, start, end)

    metrics = report.metrics_json
    assert metrics["action_item_completion_rate"] == pytest.approx(4 / 10)
    assert metrics["overdue_action_items"] == 2  # the old meeting's too
    assert metrics["carried_over_action_items"] == 5
    assert metrics["action_item_confirmation_rate"] == pytest.approx(0.9)
    assert metrics["action_progress_as_of"] is not None
    assert "액션 아이템 완료율 (최근 4주 회의): 40%" in report.body_markdown
    assert "기한 지난 항목 2건" in report.body_markdown
    assert "이월된 항목 5건" in report.body_markdown
    assert "90%" not in report.body_markdown  # the confirmation rate is not 완료율


def test_items_left_undone_are_reported_in_a_week_without_meetings(
    db_session: Session, team: str
) -> None:
    start, end = _this_week()
    earlier = _held(db_session, team, 10)
    others = [_held(db_session, team, days) for days in (11, 12)]
    _progress(
        db_session,
        team,
        [
            {"meeting_id": earlier, "confirmed": 3, "done": 0, "overdue": 2},
            *({"meeting_id": m, "confirmed": 1, "done": 0, "overdue": 0} for m in others),
        ],
    )

    report = service.generate_weekly_report(db_session, team, start, end)

    assert "분석된 회의가 없습니다" in report.body_markdown
    assert "이월된 항목 5건" in report.body_markdown
    assert "기한 지난 항목 2건" in report.body_markdown


def test_counts_from_fewer_than_three_meetings_are_left_out(db_session: Session, team: str) -> None:
    """The dashboard's floor (#812): one or two meetings' counts are no team total."""
    start, end = _this_week()
    meetings = [_held(db_session, team, days) for days in (2, 10)]
    _progress(
        db_session,
        team,
        [{"meeting_id": m, "confirmed": 2, "done": 1, "overdue": 1} for m in meetings],
    )

    report = service.generate_weekly_report(db_session, team, start, end)

    assert "최근 4주 회의가 3건 미만이라 완료율은 싣지 않습니다." in report.body_markdown
    assert "기한 지난 항목" not in report.body_markdown
    assert "이월된 항목" not in report.body_markdown
    assert report.metrics_json["action_item_completion_rate"] is None
    assert report.metrics_json["carried_over_action_items"] is None


def test_counts_that_did_not_arrive_are_said_so_not_shown_as_zero(
    db_session: Session, team: str
) -> None:
    start, end = _this_week()
    meeting = _held(db_session, team, 2)
    _progress(
        db_session,
        team,
        [{"meeting_id": meeting, "confirmed": 2, "done": 2, "overdue": 0}],
        minutes_ago=31,
    )

    report = service.generate_weekly_report(db_session, team, start, end)

    assert report.metrics_json["action_item_completion_rate"] is None
    assert report.metrics_json["overdue_action_items"] is None
    assert "액션 아이템 완료 현황을 받지 못했습니다" in report.body_markdown


def test_a_past_weeks_report_leaves_todays_counts_out(db_session: Session, team: str) -> None:
    """B's counts are today's; printed in a report for an older week they would
    read as that week's."""
    meeting = _held(db_session, team, 2)
    _progress(db_session, team, [{"meeting_id": meeting, "confirmed": 2, "done": 1, "overdue": 0}])

    report = service.generate_weekly_report(db_session, team, PERIOD_START, PERIOD_END)

    assert report.metrics_json["action_item_completion_rate"] is None
    assert "완료" not in report.body_markdown
    assert "이월" not in report.body_markdown


def test_writing_a_past_week_again_keeps_the_counts_it_first_stated(
    db_session: Session, team: str
) -> None:
    """A redelivery or a run by hand a few days on would otherwise find the
    counts too old to state, and wipe what the report said (#809 review)."""
    from datetime import timedelta
    from unittest.mock import patch

    start, end = _this_week()
    meetings = [_held(db_session, team, days) for days in (2, 10, 12, 14)]
    _progress(
        db_session,
        team,
        [{"meeting_id": m, "confirmed": 2, "done": 1, "overdue": 1} for m in meetings],
    )
    first = service.generate_weekly_report(db_session, team, start, end)

    later = datetime.now(UTC) + timedelta(days=3)

    class _Later(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def, override]
            return later if tz is None else later.astimezone(tz)

    with patch.object(service, "datetime", _Later):
        again = service.generate_weekly_report(db_session, team, start, end)

    keys = (
        "action_item_completion_rate",
        "action_completion_meeting_count",
        "overdue_action_items",
        "carried_over_action_items",
        "action_progress_as_of",
    )
    assert {k: again.metrics_json[k] for k in keys} == {k: first.metrics_json[k] for k in keys}
    assert first.metrics_json["overdue_action_items"] == 4
    assert "기한 지난 항목 4건" in again.body_markdown


def test_a_meeting_on_the_weeks_first_morning_is_not_carried_into_it(
    db_session: Session, team: str
) -> None:
    """Carried over is what meetings before the week left undone, and the week
    starts at midnight Korean time, not UTC (#809 review)."""
    from datetime import timedelta

    from autune_core import Meeting

    start, end = _this_week()
    early = Meeting(
        team_id=team,
        title="m",
        started_at=datetime.combine(start, datetime.min.time(), tzinfo=service._KST)
        + timedelta(minutes=30),
    )
    db_session.add(early)
    db_session.flush()
    earlier = [_held(db_session, team, days) for days in (10, 11, 12)]
    _progress(
        db_session,
        team,
        [
            {"meeting_id": early.id, "confirmed": 5, "done": 0, "overdue": 0},
            *({"meeting_id": m, "confirmed": 1, "done": 0, "overdue": 0} for m in earlier),
        ],
    )

    report = service.generate_weekly_report(db_session, team, start, end)

    assert report.metrics_json["carried_over_action_items"] == 3
