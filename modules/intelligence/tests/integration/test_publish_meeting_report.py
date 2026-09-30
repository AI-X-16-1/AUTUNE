"""E's action for the Report subagent's proposal (agent-layer.md section 8).

Shaped like B's ``ACTIONS`` (#492): no session argument, the action owns its
transaction, and ``team_id`` comes from the run's authenticated scope.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from autune_intelligence import service, tools
from autune_intelligence.models import IntelMeetingReport

BODY = "✅ 확정된 액션 아이템\n• 결제 API 스펙 초안 — 백엔드 · 10/2"


@pytest.fixture
def events(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """The action's own transaction is the test session; record commit and enqueue order."""
    recorded: list[tuple[str, str]] = []

    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        yield db_session
        recorded.append(("commit", ""))

    def enqueue(args: tuple[str, ...]) -> None:
        recorded.append(("enqueue", args[0]))

    monkeypatch.setattr(tools, "session_scope", scope)
    monkeypatch.setattr(tools.tasks.deliver_meeting_report, "apply_async", enqueue)
    return recorded


def _meeting(db_session: Session, team: str, title: str) -> str:
    from autune_core import Meeting

    row = Meeting(team_id=team, title=title, started_at=datetime(2026, 9, 29, 5, 0, tzinfo=UTC))
    db_session.add(row)
    db_session.flush()
    return row.id


@pytest.mark.usefixtures("events")
def test_publish_adds_the_header_and_footer_around_the_body(db_session: Session, team: str) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")

    result = tools.publish_meeting_report(team, meeting, BODY)

    assert result["ok"] is True
    # The same shape as B's actions: the changed thing, by id.
    assert result["items"] == [
        {"title": "리포트를 저장했고 발송을 예약했습니다.", "body": "", "score": 1.0, "id": meeting}
    ]
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    assert row.body_markdown == (
        "📋 결제 기능 기획 · 9/29\n\n" + BODY + "\n\n자동 생성된 리포트입니다."
    )


@pytest.mark.usefixtures("events")
def test_a_title_with_personal_data_is_left_out_of_the_header(
    db_session: Session, team: str
) -> None:
    meeting = _meeting(db_session, team, "kim@example.com 1:1")

    tools.publish_meeting_report(team, meeting, BODY)

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.body_markdown.startswith("📋 회의 리포트 · 9/29\n")


def test_delivery_is_enqueued_after_the_commit_with_the_id_only(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")

    tools.publish_meeting_report(team, meeting, BODY, pending_review=True)

    assert events == [("commit", ""), ("enqueue", meeting)]
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.pending_review is True


def test_another_teams_meeting_is_refused(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    from autune_core import Team

    other = Team(name="Other Team")
    db_session.add(other)
    db_session.flush()
    meeting = _meeting(db_session, other.id, "남의 회의")

    result = tools.publish_meeting_report(team, meeting, BODY)

    assert result["ok"] is False
    assert db_session.get(IntelMeetingReport, meeting) is None
    assert ("enqueue", meeting) not in events


def _enqueued(events: list[tuple[str, str]]) -> list[str]:
    return [ident for kind, ident in events if kind == "enqueue"]


def test_an_unknown_meeting_is_refused(team: str, events: list[tuple[str, str]]) -> None:
    result = tools.publish_meeting_report(team, "mtg_doesnotexist", BODY)

    assert result["ok"] is False
    assert _enqueued(events) == []


def test_a_posted_report_is_refused(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")
    service.save_meeting_report(db_session, meeting, BODY)
    service.claim_meeting_report(db_session, meeting)

    result = tools.publish_meeting_report(team, meeting, BODY)

    assert result["ok"] is False and result["reason"] == "already posted"
    assert _enqueued(events) == []


def test_a_document_over_the_cap_is_refused(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")

    result = tools.publish_meeting_report(team, meeting, "가" * 3000)

    assert result["ok"] is False and result["reason"] == "report too long"
    assert db_session.get(IntelMeetingReport, meeting) is None
    assert _enqueued(events) == []


def test_a_body_with_personal_data_is_refused_by_category_not_raised(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    """A model-written body failing the mask is an expected route, not a bug in E."""
    meeting = _meeting(db_session, team, "결제 기능 기획")

    result = tools.publish_meeting_report(team, meeting, "담당 연락처 010-1234-5678")

    assert result["ok"] is False
    assert result["reason"].startswith("unmasked personal data")
    assert "010" not in str(result)  # categories only, never the text
    assert db_session.get(IntelMeetingReport, meeting) is None
    assert _enqueued(events) == []


def test_saving_a_report_locks_its_row(db_session: Session, team: str) -> None:
    """A re-publish racing the deliver task's claim must wait on the row, not
    overwrite a report that was just posted."""
    meeting = _meeting(db_session, team, "결제 기능 기획")
    service.save_meeting_report(db_session, meeting, BODY)
    statements: list[str] = []

    def capture(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        service.save_meeting_report(db_session, meeting, "고친 본문")
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert any("intel_meeting_reports" in s and "FOR UPDATE" in s for s in statements), statements


def test_a_broker_failure_is_logged_not_raised(
    db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row is committed by then; the caller must not be told it failed."""

    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        yield db_session

    def down(_args: tuple[str, ...]) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(tools, "session_scope", scope)
    monkeypatch.setattr(tools.tasks.deliver_meeting_report, "apply_async", down)
    meeting = _meeting(db_session, team, "결제 기능 기획")

    result = tools.publish_meeting_report(team, meeting, BODY)

    assert result["ok"] is True
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.sent_at is None


def test_the_action_is_not_offered_as_a_tool() -> None:
    """A model calls ``TOOLS``; an action runs only when the main agent executes it."""
    assert [tools.publish_meeting_report] == tools.ACTIONS
    assert not set(tools.ACTIONS) & set(tools.TOOLS)
    # B's form ("L2 -- runs only after a person approves"), so one reader parses both.
    assert "L1 -- runs without approval" in (tools.publish_meeting_report.__doc__ or "")


def test_the_run_fills_team_id_never_the_model() -> None:
    assert tools.RUN_SCOPE == ("team_id",)
