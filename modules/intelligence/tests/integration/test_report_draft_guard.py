"""A re-draft asked in chat is checked again when it runs (spec section 4).

L1 runs after the answer, so a member can edit the draft in between. With
``expected_draft_id`` the save refuses unless the stored draft is still the one
read and no member has edited it.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from autune_core import Meeting, TeamMember, User
from autune_intelligence import service, tools
from autune_intelligence.models import IntelMeetingReport

BODY = "✅ 확정된 액션 아이템\n• 결제 API 스펙 초안 — 백엔드 · 10/2"


@pytest.fixture
def scoped(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(tools, "session_scope", scope)


@pytest.fixture
def member(db_session: Session, team: str) -> str:
    user = User(email=f"m-{uuid.uuid4().hex}@example.com", display_name="고친 사람")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user.id


def _meeting(db_session: Session, team: str) -> str:
    row = Meeting(team_id=team, title="결제 회의", started_at=datetime(2026, 10, 2, 5, tzinfo=UTC))
    db_session.add(row)
    db_session.flush()
    return row.id


def _store(db_session: Session, meeting_id: str, draft_id: str) -> None:
    row = db_session.get(Meeting, meeting_id)
    assert row is not None
    service.save_meeting_report(
        db_session, meeting_id, service.meeting_report_document(row, BODY), draft_id=draft_id
    )


@pytest.mark.usefixtures("scoped")
def test_a_redraft_runs_when_the_draft_is_still_the_one_read(
    db_session: Session, team: str
) -> None:
    meeting = _meeting(db_session, team)
    _store(db_session, meeting, "rdr_a")

    result = tools.draft_meeting_report(
        team, meeting, BODY + "\n• 추가", draft_id="rdr_b", replaces_draft_id="rdr_a"
    )

    assert result["ok"] is True
    stored = db_session.get(IntelMeetingReport, meeting)
    assert stored is not None and stored.draft_id == "rdr_b"


@pytest.mark.usefixtures("scoped")
def test_a_redraft_after_a_members_edit_is_refused_and_the_edit_stays(
    db_session: Session, team: str, member: str
) -> None:
    meeting = _meeting(db_session, team)
    _store(db_session, meeting, "rdr_a")
    service.edit_meeting_report(db_session, meeting, BODY + "\n• 사람이 고침", user_id=member)
    edited = db_session.get(IntelMeetingReport, meeting)
    assert edited is not None
    edited_body = edited.body_markdown

    result = tools.draft_meeting_report(
        team, meeting, BODY, draft_id="rdr_b", replaces_draft_id="rdr_a"
    )

    assert (result["ok"], result["reason"]) == (False, "draft changed")
    db_session.refresh(edited)
    assert edited.body_markdown == edited_body and edited.edited_by == member


@pytest.mark.usefixtures("scoped")
def test_a_redraft_after_another_run_replaced_the_draft_is_refused(
    db_session: Session, team: str
) -> None:
    meeting = _meeting(db_session, team)
    _store(db_session, meeting, "rdr_a")
    _store(db_session, meeting, "rdr_late")  # the automatic path, in between

    result = tools.draft_meeting_report(
        team, meeting, BODY, draft_id="rdr_b", replaces_draft_id="rdr_a"
    )

    assert result["reason"] == "draft changed"


@pytest.mark.usefixtures("scoped")
def test_without_a_guard_the_template_path_still_replaces_the_draft(
    db_session: Session, team: str, member: str
) -> None:
    """Today's behaviour, unchanged (spec section 8 leaves it to the owner)."""
    meeting = _meeting(db_session, team)
    _store(db_session, meeting, "rdr_a")
    service.edit_meeting_report(db_session, meeting, BODY + "\n• 사람이 고침", user_id=member)

    result = tools.draft_meeting_report(team, meeting, BODY, draft_id="rdr_b")

    assert result["ok"] is True
