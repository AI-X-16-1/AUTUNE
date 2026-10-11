"""E's reads for the E agent (spec section 3): reports, alignment, weekly."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from autune_core import Meeting, Team
from autune_intelligence import service, tools
from autune_intelligence.models import (
    IntelAlignment,
    IntelMeetingReport,
    IntelReport,
)

KST = timezone(timedelta(hours=9))
BODY = "✅ 확정된 할 일\n• 결제 API 스펙 초안 — 백엔드 · 10/2"


def _report(db_session: Session, team: str, title: str, held: datetime, draft: str) -> str:
    meeting = Meeting(team_id=team, title=title, started_at=held)
    db_session.add(meeting)
    db_session.flush()
    service.save_meeting_report(
        db_session, meeting.id, service.meeting_report_document(meeting, BODY), draft_id=draft
    )
    return meeting.id


def test_meeting_reports_filters_by_korean_date(db_session: Session, team: str) -> None:
    """UTC and KST disagree on both meetings (Review Focus 1).

    08:00 KST on the 5th is 23:00Z on the 4th: in the 5th by Korean date, not by UTC.
    00:30 KST on the 6th is 15:30Z on the 5th: in the 6th by Korean date, not by UTC.
    """
    early = _report(db_session, team, "이른 회의", datetime(2026, 10, 5, 8, 0, tzinfo=KST), "rdr_a")
    _report(db_session, team, "다음날 회의", datetime(2026, 10, 6, 0, 30, tzinfo=KST), "rdr_b")

    result = tools.meeting_reports(db_session, team, since="2026-10-05", until="2026-10-05")

    assert [i["meeting_id"] for i in result["items"]] == [early]
    assert result["items"][0]["date"] == "2026-10-05"


def test_meeting_reports_finds_by_title_and_says_status(db_session: Session, team: str) -> None:
    held = datetime(2026, 10, 2, 5, tzinfo=UTC)
    payment = _report(db_session, team, "결제 회의", held, "rdr_a")
    _report(db_session, team, "디자인 회의", held, "rdr_b")

    result = tools.meeting_reports(db_session, team, title_contains="결제")

    (item,) = result["items"]
    assert (item["meeting_id"], item["status"], item["draft_id"]) == (payment, "draft", "rdr_a")
    assert len(item["body"]) <= 120


def test_meeting_reports_never_lists_another_teams(db_session: Session, team: str) -> None:
    other = Team(name="Other")
    db_session.add(other)
    db_session.flush()
    _report(db_session, other.id, "남의 회의", datetime(2026, 10, 2, tzinfo=UTC), "rdr_x")

    assert tools.meeting_reports(db_session, team)["items"] == []


def test_meeting_report_body_cuts_at_1500_and_keeps_the_ids(db_session: Session, team: str) -> None:
    meeting = _report(db_session, team, "결제 회의", datetime(2026, 10, 2, tzinfo=UTC), "rdr_a")
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    row.body_markdown = f"{row.body_markdown}\n{'가' * 2000}"
    db_session.flush()

    result = tools.meeting_report_body(db_session, team, meeting)

    (item,) = result["items"]
    assert (item["id"], item["draft_id"], item["status"]) == (meeting, "rdr_a", "draft")
    assert len(item["body"]) == 1500 and "결제 API 스펙" in item["body"]


def test_meeting_report_body_finds_an_old_report_among_many(db_session: Session, team: str) -> None:
    old = _report(db_session, team, "옛 회의", datetime(2025, 1, 2, tzinfo=UTC), "rdr_old")
    for day in range(1, 4):
        _report(db_session, team, f"회의 {day}", datetime(2026, 10, day, tzinfo=UTC), f"rdr_{day}")

    (item,) = tools.meeting_report_body(db_session, team, old)["items"]

    assert item["id"] == old and item["date"] == "2025-01-02"


def test_a_posted_report_says_posted(db_session: Session, team: str) -> None:
    meeting = _report(db_session, team, "결제 회의", datetime(2026, 10, 2, tzinfo=UTC), "rdr_a")
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    row.sent_at = datetime(2026, 10, 3, tzinfo=UTC)
    db_session.flush()

    (listed,) = tools.meeting_reports(db_session, team)["items"]
    (body,) = tools.meeting_report_body(db_session, team, meeting)["items"]

    assert listed["status"] == "posted" and body["status"] == "posted"


def test_a_report_item_says_it_opens_the_report(db_session: Session, team: str) -> None:
    """A listed report links to its dashboard card, not the meeting screen."""
    meeting = _report(db_session, team, "결제 회의", datetime(2026, 10, 2, tzinfo=UTC), "rdr_a")

    (listed,) = tools.meeting_reports(db_session, team)["items"]
    (body,) = tools.meeting_report_body(db_session, team, meeting)["items"]

    assert listed["link"] == "report" and body["link"] == "report"
    assert body["meeting_id"] == meeting


def test_meeting_report_body_refuses_another_teams_meeting(db_session: Session, team: str) -> None:
    other = Team(name="Other")
    db_session.add(other)
    db_session.flush()
    theirs = _report(db_session, other.id, "남의 회의", datetime(2026, 10, 2, tzinfo=UTC), "rdr_x")

    result = tools.meeting_report_body(db_session, team, theirs)

    assert result["ok"] is False and result["reason"] == "meeting not found"
    assert result["items"] == []


def test_meeting_report_body_for_a_meeting_without_a_report(db_session: Session, team: str) -> None:
    held = datetime(2026, 10, 2, tzinfo=UTC)
    meeting = Meeting(team_id=team, title="리포트 없는 회의", started_at=held)
    db_session.add(meeting)
    db_session.flush()

    result = tools.meeting_report_body(db_session, team, meeting.id)

    assert result["ok"] is True and result["items"] == []


def test_meeting_reports_refuses_a_bad_date(db_session: Session, team: str) -> None:
    result = tools.meeting_reports(db_session, team, since="2026-13-01")

    assert result["ok"] is False and result["reason"] == "bad date"


def test_meeting_reports_title_filter_is_not_a_pattern(db_session: Session, team: str) -> None:
    _report(db_session, team, "500 회의", datetime(2026, 10, 2, tzinfo=UTC), "rdr_a")

    assert tools.meeting_reports(db_session, team, title_contains="50%")["items"] == []


def test_meeting_reports_returns_at_most_limit_and_says_when_more_exist(
    db_session: Session, team: str
) -> None:
    for day in (1, 2, 3):
        _report(
            db_session, team, f"회의 {day}", datetime(2026, 10, day, 5, tzinfo=UTC), f"rdr_{day}"
        )

    result = tools.meeting_reports(db_session, team, limit=2)

    assert len(result["items"]) == 2 and result["truncated"] is True


def test_role_alignment_keeps_the_heatmaps_three_meeting_floor(
    db_session: Session, team: str
) -> None:
    for i in range(3):
        m = Meeting(team_id=team, title=f"m{i}")
        db_session.add(m)
        db_session.flush()
        db_session.add(
            IntelAlignment(meeting_id=m.id, team_id=team, role_a="PM", role_b="Dev", score=0.6)
        )
    one = Meeting(team_id=team, title="one")
    db_session.add(one)
    db_session.flush()
    db_session.add(
        IntelAlignment(meeting_id=one.id, team_id=team, role_a="PM", role_b="Design", score=0.9)
    )
    db_session.flush()

    result = tools.role_alignment(db_session, team)

    assert [i["title"] for i in result["items"]] == ["PM · Dev"]


def test_weekly_reports_reads_the_week_holding_a_date(db_session: Session, team: str) -> None:
    for start in (date(2026, 9, 28), date(2026, 10, 5)):
        db_session.add(
            IntelReport(
                team_id=team,
                period_start=start,
                period_end=start + timedelta(days=7),
                body_markdown=f"*{start} 주간 리포트*",
                metrics_json={},
                source_meeting_ids=[],
            )
        )
    db_session.flush()

    latest = tools.weekly_reports(db_session, team)
    asked = tools.weekly_reports(db_session, team, on="2026-09-30")

    assert latest["items"][0]["title"].startswith("2026-10-05")
    assert asked["items"][0]["title"].startswith("2026-09-28")
    assert "went_out" in asked["items"][0]


def test_weekly_report_schedule_says_the_day_and_hour(db_session: Session, team: str) -> None:
    result = tools.weekly_report_schedule(db_session, team)

    assert "월요일 09:00" in result["summary"]


def test_the_schedule_action_records_the_asker(
    db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib
    import uuid

    from autune_core import TeamMember, User

    @contextlib.contextmanager
    def scope():
        yield db_session

    monkeypatch.setattr(tools, "session_scope", scope)
    user = User(email=f"a-{uuid.uuid4().hex}@example.com", display_name="요청한 사람")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()

    result = tools.set_weekly_report_schedule(team, user.id, weekday=4, hour=18)

    assert result["ok"] is True
    assert service.weekly_report_schedule(db_session, team).updated_by_name == "요청한 사람"


def test_the_schedule_action_refuses_a_non_member_and_a_bad_hour(
    db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib
    import uuid

    from autune_core import User

    @contextlib.contextmanager
    def scope():
        yield db_session

    monkeypatch.setattr(tools, "session_scope", scope)
    outsider = User(email=f"o-{uuid.uuid4().hex}@example.com", display_name="밖")
    db_session.add(outsider)
    db_session.flush()

    assert tools.set_weekly_report_schedule(team, outsider.id, weekday=1, hour=9)["ok"] is False
    assert tools.set_weekly_report_schedule(team, outsider.id, weekday=1, hour=24)["ok"] is False


def test_the_schedule_action_is_l1() -> None:
    assert tools.set_weekly_report_schedule in tools.L1_ACTIONS
    assert tools.set_weekly_report_schedule in tools.ACTIONS


def test_the_schedule_action_keeps_send_empty_when_it_is_left_out(
    db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib
    import uuid

    from autune_core import TeamMember, User

    @contextlib.contextmanager
    def scope():
        yield db_session

    monkeypatch.setattr(tools, "session_scope", scope)
    user = User(email=f"k-{uuid.uuid4().hex}@example.com", display_name="요청한 사람")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    service.set_weekly_report_schedule(
        db_session, team, weekday=0, hour=9, send_empty=True, user_id=user.id
    )

    result = tools.set_weekly_report_schedule(team, user.id, weekday=4, hour=18)

    assert result["ok"] is True
    kept = service.weekly_report_schedule(db_session, team)
    assert (kept.weekday, kept.hour, kept.send_empty) == (4, 18, True)
