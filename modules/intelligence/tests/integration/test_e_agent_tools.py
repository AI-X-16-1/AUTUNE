"""E's reads for the E agent (spec section 3): reports, alignment, weekly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from autune_core import Meeting
from autune_intelligence import service, tools

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
    assert "body" not in item or len(item["body"]) <= 120


def test_meeting_reports_never_lists_another_teams(db_session: Session, team: str) -> None:
    from autune_core import Team

    other = Team(name="Other")
    db_session.add(other)
    db_session.flush()
    _report(db_session, other.id, "남의 회의", datetime(2026, 10, 2, tzinfo=UTC), "rdr_x")

    assert tools.meeting_reports(db_session, team)["items"] == []


def test_meeting_report_body_cuts_at_1500_and_keeps_the_ids(db_session: Session, team: str) -> None:
    meeting = _report(db_session, team, "결제 회의", datetime(2026, 10, 2, tzinfo=UTC), "rdr_a")

    result = tools.meeting_report_body(db_session, team, meeting)

    (item,) = result["items"]
    assert (item["id"], item["draft_id"], item["status"]) == (meeting, "rdr_a", "draft")
    assert len(item["body"]) <= 1500 and "결제 API 스펙" in item["body"]


def test_meeting_reports_returns_at_most_limit_and_says_when_more_exist(
    db_session: Session, team: str
) -> None:
    for day in (1, 2, 3):
        _report(
            db_session, team, f"회의 {day}", datetime(2026, 10, day, 5, tzinfo=UTC), f"rdr_{day}"
        )

    result = tools.meeting_reports(db_session, team, limit=2)

    assert len(result["items"]) == 2 and result["truncated"] is True
