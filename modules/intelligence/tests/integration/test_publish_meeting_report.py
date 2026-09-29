"""E's write tool for the Report subagent's proposal (agent-layer.md section 8)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy.orm import Session

from autune_intelligence import service, tasks, tools
from autune_intelligence.models import IntelMeetingReport

BODY = "✅ 확정된 액션 아이템\n• 결제 API 스펙 초안 — 백엔드 · 10/2"


def _meeting(db_session: Session, team: str, title: str) -> str:
    from autune_core import Meeting

    row = Meeting(team_id=team, title=title, started_at=datetime(2026, 9, 29, 5, 0, tzinfo=UTC))
    db_session.add(row)
    db_session.flush()
    return row.id


def test_publish_adds_the_header_and_footer_around_the_body(db_session: Session, team: str) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")

    with patch.object(tasks.deliver_meeting_report, "apply_async"):
        result = tools.publish_meeting_report(db_session, meeting, BODY)

    assert result["ok"] is True
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    assert row.body_markdown == (
        "📋 결제 기능 기획 · 9/29\n\n" + BODY + "\n\n자동 생성된 리포트입니다."
    )


def test_a_title_with_personal_data_is_left_out_of_the_header(
    db_session: Session, team: str
) -> None:
    meeting = _meeting(db_session, team, "kim@example.com 1:1")

    with patch.object(tasks.deliver_meeting_report, "apply_async"):
        tools.publish_meeting_report(db_session, meeting, BODY)

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.body_markdown.startswith("📋 회의 리포트 · 9/29\n")


def test_delivery_is_enqueued_only_after_the_commit_with_the_id_only(
    db_session: Session, team: str
) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")

    with patch.object(tasks.deliver_meeting_report, "apply_async") as enqueue:
        tools.publish_meeting_report(db_session, meeting, BODY, pending_review=True)
        enqueue.assert_not_called()
        db_session.commit()

    enqueue.assert_called_once_with((meeting,))
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.pending_review is True


def test_a_rolled_back_publish_is_never_enqueued_by_a_later_commit(
    db_session: Session, team: str
) -> None:
    """A caller that reuses its session after a rollback must not post that report."""
    meeting = _meeting(db_session, team, "결제 기능 기획")
    db_session.commit()

    with patch.object(tasks.deliver_meeting_report, "apply_async") as enqueue:
        tools.publish_meeting_report(db_session, meeting, BODY)
        db_session.rollback()
        db_session.commit()

    enqueue.assert_not_called()


def test_two_publishes_in_one_transaction_enqueue_once(db_session: Session, team: str) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")

    with patch.object(tasks.deliver_meeting_report, "apply_async") as enqueue:
        tools.publish_meeting_report(db_session, meeting, BODY)
        tools.publish_meeting_report(db_session, meeting, BODY)
        db_session.commit()

    enqueue.assert_called_once_with((meeting,))


def test_a_broker_failure_after_commit_does_not_fail_the_commit(
    db_session: Session, team: str
) -> None:
    """The row is committed by then; a raise here would tell the caller it was not."""
    meeting = _meeting(db_session, team, "결제 기능 기획")

    with patch.object(
        tasks.deliver_meeting_report, "apply_async", side_effect=ConnectionError("broker down")
    ):
        tools.publish_meeting_report(db_session, meeting, BODY)
        db_session.commit()

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.sent_at is None


def test_publish_is_not_ok_for_an_unknown_meeting(db_session: Session) -> None:
    result = tools.publish_meeting_report(db_session, "mtg_doesnotexist", BODY)

    assert result["ok"] is False


def test_publish_is_not_ok_once_the_report_was_posted(db_session: Session, team: str) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")
    service.save_meeting_report(db_session, meeting, BODY)
    service.claim_meeting_report(db_session, meeting)

    result = tools.publish_meeting_report(db_session, meeting, BODY)

    assert result["ok"] is False and result["reason"] == "already posted"


def test_publish_is_not_ok_when_the_document_is_too_long(db_session: Session, team: str) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")

    result = tools.publish_meeting_report(db_session, meeting, "가" * 3000)

    assert result["ok"] is False and result["reason"] == "report too long"


def test_the_write_tool_is_not_a_read_tool() -> None:
    assert tools.publish_meeting_report in tools.WRITE_TOOLS
    assert tools.publish_meeting_report not in tools.TOOLS
