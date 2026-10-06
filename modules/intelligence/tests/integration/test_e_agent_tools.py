"""E's reads for the E agent (spec section 3): reports, alignment, weekly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from autune_core import Meeting, Team
from autune_intelligence import service, tools
from autune_intelligence.models import IntelMeetingReport

KST = timezone(timedelta(hours=9))
BODY = "✅ 확정된 액션 아이템\n• 결제 API 스펙 초안 — 백엔드 · 10/2"


def _report(db_session: Session, team: str, title: str, held: datetime, draft: str) -> str:
    meeting = Meeting(team_id=team, title=title, started_at=held)
    db_session.add(meeting)
    db_session.flush()
    service.save_meeting_report(
        db_session, meeting.id, service.meeting_report_document(meeting, BODY), draft_id=draft
    )
    return meeting.id


def test_meeting_reports_filters_by_korean_date(db_session: Session, team: str) -> None:
    """23:30 KST on the 5th is the 5th, though it is the 5th 14:30 UTC (Review Focus 1)."""
    late = _report(
        db_session, team, "늦은 회의", datetime(2026, 10, 5, 23, 30, tzinfo=KST), "rdr_a"
    )
    _report(db_session, team, "다음날 회의", datetime(2026, 10, 6, 9, 0, tzinfo=KST), "rdr_b")

    result = tools.meeting_reports(db_session, team, since="2026-10-05", until="2026-10-05")

    assert [i["meeting_id"] for i in result["items"]] == [late]
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
