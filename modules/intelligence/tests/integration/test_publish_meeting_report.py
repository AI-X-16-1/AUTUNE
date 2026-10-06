"""E's two actions for the Report subagent (agent-layer.md section 8, #509).

``draft_meeting_report`` stores a report (L1, in ``L1_ACTIONS``);
``publish_meeting_report`` posts a stored one (L2, approval first). Shaped like
B's ``ACTIONS`` (#492): no session argument, each owns its transaction, and
``team_id`` comes from the run's authenticated scope.
"""

from __future__ import annotations

import contextlib
import re
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
    """The actions' own transactions are the test session; record commit and enqueue order."""
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


def _enqueued(events: list[tuple[str, str]]) -> list[str]:
    return [ident for kind, ident in events if kind == "enqueue"]


def _meeting(db_session: Session, team: str, title: str) -> str:
    from autune_core import Meeting

    row = Meeting(team_id=team, title=title, started_at=datetime(2026, 9, 29, 5, 0, tzinfo=UTC))
    db_session.add(row)
    db_session.flush()
    return row.id


def _other_team_meeting(db_session: Session) -> str:
    from autune_core import Team

    other = Team(name="Other Team")
    db_session.add(other)
    db_session.flush()
    return _meeting(db_session, other.id, "남의 회의")


# --- draft (L1): store only ----------------------------------------------------


def test_a_draft_is_stored_with_header_and_footer_and_nothing_is_posted(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")

    result = tools.draft_meeting_report(team, meeting, BODY, pending_review=True)

    assert result["ok"] is True
    # The same shape as B's actions: the changed thing, by id.
    assert result["items"] == [
        {"title": "리포트 초안을 저장했습니다.", "body": "", "score": 1.0, "id": meeting}
    ]
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    assert row.body_markdown.startswith("📋 결제 기능 기획 · 9/29\n\n" + BODY + "\n\n")
    # The draft is a snapshot of B, C and D as of now; the footer says when.
    footer = r"\n\n자동 생성된 리포트입니다 · \d{1,2}/\d{1,2} \d{2}:\d{2} 기준\.$"
    assert re.search(footer, row.body_markdown)
    assert row.pending_review is True
    assert row.sent_at is None
    assert _enqueued(events) == []


@pytest.mark.usefixtures("events")
def test_a_title_with_personal_data_is_left_out_of_the_header(
    db_session: Session, team: str
) -> None:
    meeting = _meeting(db_session, team, "kim@example.com 1:1")

    tools.draft_meeting_report(team, meeting, BODY)

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.body_markdown.startswith("📋 회의 리포트 · 9/29\n")


@pytest.mark.usefixtures("events")
def test_a_draft_for_another_teams_meeting_is_refused(db_session: Session, team: str) -> None:
    meeting = _other_team_meeting(db_session)

    result = tools.draft_meeting_report(team, meeting, BODY)

    assert result["ok"] is False
    # The same answer as #449's scope check, and never the id the model wrote.
    assert result["reason"] == "meeting not found"
    assert meeting not in str(result) and team not in str(result)
    assert db_session.get(IntelMeetingReport, meeting) is None


@pytest.mark.usefixtures("events")
def test_a_draft_for_an_unknown_meeting_is_refused(team: str) -> None:
    assert tools.draft_meeting_report(team, "mtg_doesnotexist", BODY)["ok"] is False


@pytest.mark.usefixtures("events")
def test_a_draft_over_a_posted_report_is_refused(db_session: Session, team: str) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")
    service.save_meeting_report(db_session, meeting, BODY)
    service.claim_meeting_report(db_session, meeting)

    result = tools.draft_meeting_report(team, meeting, BODY)

    assert result["ok"] is False and result["reason"] == "already posted"


@pytest.mark.usefixtures("events")
def test_a_draft_over_the_cap_is_refused(db_session: Session, team: str) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")

    result = tools.draft_meeting_report(team, meeting, "가" * 3000)

    assert result["ok"] is False and result["reason"] == "report too long"
    assert db_session.get(IntelMeetingReport, meeting) is None


@pytest.mark.usefixtures("events")
def test_a_draft_with_personal_data_is_refused_by_category_not_raised(
    db_session: Session, team: str
) -> None:
    """A model-written body failing the mask is an expected route, not a bug in E."""
    meeting = _meeting(db_session, team, "결제 기능 기획")

    result = tools.draft_meeting_report(team, meeting, "담당 연락처 010-1234-5678")

    assert result["ok"] is False
    assert result["reason"].startswith("unmasked personal data")
    assert "010" not in str(result)  # categories only, never the text
    assert db_session.get(IntelMeetingReport, meeting) is None


# --- publish (L2): post the stored draft -----------------------------------------


def test_publish_enqueues_after_the_commit_with_the_id_only(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")
    service.save_meeting_report(db_session, meeting, BODY)

    result = tools.publish_meeting_report(team, meeting)

    assert result["ok"] is True
    assert result["items"][0]["id"] == meeting
    assert events == [("commit", ""), ("enqueue", meeting)]


def test_a_draft_keeps_the_id_the_subagent_gave_it(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")

    tools.draft_meeting_report(team, meeting, BODY, draft_id="rdr_first")
    tools.draft_meeting_report(team, meeting, BODY + "\n추가", draft_id="rdr_second")

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.draft_id == "rdr_second"


def test_publish_of_a_replaced_draft_is_refused(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    """Approving an older proposal must not post a draft written after it (review of #508)."""
    meeting = _meeting(db_session, team, "결제 기능 기획")
    tools.draft_meeting_report(team, meeting, BODY, draft_id="rdr_first")
    tools.draft_meeting_report(team, meeting, BODY + "\n추가", draft_id="rdr_second")

    result = tools.publish_meeting_report(team, meeting, draft_id="rdr_first")

    assert result["ok"] is False and result["reason"] == "draft not current"
    assert _enqueued(events) == []


def test_publish_after_this_runs_draft_was_refused_posts_nothing(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    """The run's L1 draft was refused, so an earlier run's draft is still stored.

    The approval names this run's draft, which never existed; posting the
    earlier one would put text under an approval it was not given for. The
    words must not claim a newer draft exists (review of #570).
    """
    meeting = _meeting(db_session, team, "결제 기능 기획")
    tools.draft_meeting_report(team, meeting, BODY, draft_id="rdr_first")
    refused = tools.draft_meeting_report(
        team, meeting, BODY + "\n연락처 010-1234-5678", draft_id="rdr_second"
    )
    assert refused["ok"] is False

    result = tools.publish_meeting_report(team, meeting, draft_id="rdr_second")

    assert result["ok"] is False and result["reason"] == "draft not current"
    assert "저장되지 않았습니다" in result["summary"]
    assert _enqueued(events) == []


def test_publish_hands_the_task_the_draft_id_it_was_approved_for(
    db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The draft can still change between this check and the task's claim; the claim checks too."""

    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        yield db_session

    sent: list[tuple[str, ...]] = []
    monkeypatch.setattr(tools, "session_scope", scope)
    monkeypatch.setattr(tools.tasks.deliver_meeting_report, "apply_async", sent.append)
    meeting = _meeting(db_session, team, "결제 기능 기획")
    tools.draft_meeting_report(team, meeting, BODY, draft_id="rdr_first")

    result = tools.publish_meeting_report(team, meeting, draft_id="rdr_first")

    assert result["ok"] is True
    assert sent == [(meeting, "rdr_first")]


def test_publish_without_a_draft_is_refused(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")

    result = tools.publish_meeting_report(team, meeting)

    assert result["ok"] is False and result["reason"] == "no draft"
    assert _enqueued(events) == []


def test_publish_of_a_posted_report_is_refused(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    meeting = _meeting(db_session, team, "결제 기능 기획")
    service.save_meeting_report(db_session, meeting, BODY)
    service.claim_meeting_report(db_session, meeting)

    result = tools.publish_meeting_report(team, meeting)

    assert result["ok"] is False and result["reason"] == "already posted"
    assert _enqueued(events) == []


def test_publish_for_another_teams_meeting_is_refused(
    db_session: Session, team: str, events: list[tuple[str, str]]
) -> None:
    meeting = _other_team_meeting(db_session)
    service.save_meeting_report(db_session, meeting, BODY)

    result = tools.publish_meeting_report(team, meeting)

    assert result["ok"] is False
    assert _enqueued(events) == []


def test_a_broker_failure_is_logged_not_raised(
    db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The draft is committed; the caller must not be told publishing failed outright."""

    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        yield db_session

    def down(_args: tuple[str, ...]) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(tools, "session_scope", scope)
    monkeypatch.setattr(tools.tasks.deliver_meeting_report, "apply_async", down)
    meeting = _meeting(db_session, team, "결제 기능 기획")
    service.save_meeting_report(db_session, meeting, BODY)

    result = tools.publish_meeting_report(team, meeting)

    assert result["ok"] is True
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.sent_at is None


# --- shared ----------------------------------------------------------------------


def test_saving_a_report_locks_its_row(db_session: Session, team: str) -> None:
    """A re-draft racing the deliver task's claim must wait on the row, not
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


def test_the_module_sets_the_levels_not_the_subagent() -> None:
    """#509: a module lists its writes in ACTIONS and the reversible ones in L1_ACTIONS."""
    assert [
        tools.draft_meeting_report,
        tools.publish_meeting_report,
        tools.publish_meeting_report_correction,  # #674
        tools.set_weekly_report_schedule,  # the E agent
    ] == tools.ACTIONS
    assert [tools.draft_meeting_report, tools.set_weekly_report_schedule] == tools.L1_ACTIONS
    assert not set(tools.ACTIONS) & set(tools.TOOLS)
    # B's form, so one reader parses both modules' docstrings.
    for action in tools.L1_ACTIONS:
        assert "L1 -- runs without approval" in (action.__doc__ or "")
    for action in (tools.publish_meeting_report, tools.publish_meeting_report_correction):
        assert "L2 -- runs only after a person approves" in (action.__doc__ or "")


def test_the_run_fills_team_id_never_the_model() -> None:
    assert tools.RUN_SCOPE == ("team_id",)
