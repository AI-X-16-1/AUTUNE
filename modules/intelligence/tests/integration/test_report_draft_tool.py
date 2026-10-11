"""``meeting_report_draft``: the stored report draft, for the approval card's preview (#571).

The approver should read the text a "리포트 게시" approval would post. The card
names the draft by the id the proposal carries (#570), so a draft a later run
replaced shows nothing rather than text nobody approved.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from autune_core import Meeting, Team
from autune_intelligence import service, tools

BODY = "✅ 확정된 할 일\n• 결제 API 스펙 초안 — 백엔드 · 10/2"


def _store(db_session: Session, meeting: str, draft_id: str) -> None:
    row = db_session.get(Meeting, meeting)
    assert row is not None
    document = service.meeting_report_document(row, BODY)
    service.save_meeting_report(db_session, meeting, document, draft_id=draft_id)


def test_the_current_draft_comes_back_with_its_title_and_body(
    db_session: Session, team: str, meeting: str
) -> None:
    _store(db_session, meeting, "rdr_seen")

    result = tools.meeting_report_draft(db_session, team, meeting, "rdr_seen")

    assert result["ok"] is True
    [item] = result["items"]
    assert item["title"].startswith("Test Meeting · ")
    assert item["body"].startswith(BODY)
    assert "자동 생성된 리포트입니다" in item["body"]  # the footer stays with the body
    assert "📋" not in item["body"]  # the header line is the title, not repeated
    assert item["posted"] is False


def test_a_replaced_draft_shows_nothing(db_session: Session, team: str, meeting: str) -> None:
    """The approval names the old id; the stored text is a later run's that nobody approved."""
    _store(db_session, meeting, "rdr_newer")

    result = tools.meeting_report_draft(db_session, team, meeting, "rdr_seen")

    assert result["ok"] is True
    assert result["items"] == []


def test_a_meeting_without_a_report_shows_nothing(
    db_session: Session, team: str, meeting: str
) -> None:
    result = tools.meeting_report_draft(db_session, team, meeting, "rdr_seen")

    assert result["ok"] is True and result["items"] == []


def test_another_teams_meeting_is_not_found(db_session: Session, team: str) -> None:
    other = Team(name="Other Team")
    db_session.add(other)
    db_session.flush()
    theirs = Meeting(team_id=other.id, title="남의 회의")
    db_session.add(theirs)
    db_session.flush()
    _store(db_session, theirs.id, "rdr_theirs")

    result = tools.meeting_report_draft(db_session, team, theirs.id, "rdr_theirs")

    assert result["ok"] is False and result["items"] == []
    assert result["reason"] == "meeting not found"


def test_a_posted_draft_still_reads_and_says_so(
    db_session: Session, team: str, meeting: str
) -> None:
    _store(db_session, meeting, "rdr_seen")
    service.claim_meeting_report(db_session, meeting, draft_id="rdr_seen")

    [item] = tools.meeting_report_draft(db_session, team, meeting, "rdr_seen")["items"]

    assert item["posted"] is True


def test_the_agent_layer_collects_it_with_the_team_from_the_run() -> None:
    assert tools.meeting_report_draft in tools.TOOLS
    assert tools.RUN_SCOPE == ("team_id",)
