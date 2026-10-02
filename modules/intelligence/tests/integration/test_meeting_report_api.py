"""The dashboard's meeting-report card: list a team's reports, edit a draft (10/2).

A report is meeting text, so unlike E's older aggregate routes these two
authenticate and check team membership. A draft may be edited by any member of
the team until it is posted; the editor and the time are recorded, and the
pending approval keeps its ``draft_id`` -- the approver reads the edited text on
the card (decided with the user, 10/2). A posted report is not edited here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_core import AutuneError, Meeting, Team, TeamMember, User, get_session
from autune_core.auth import current_user
from autune_intelligence import service
from autune_intelligence.models import IntelMeetingReport
from autune_intelligence.router import router

BODY = "✅ 확정된 액션 아이템\n• 결제 API 스펙 초안 — 백엔드 · 10/2"


def _user(db_session: Session, team: str | None, name: str = "이승환") -> User:
    user = User(email=f"{name}-{datetime.now(UTC).timestamp()}@example.com", display_name=name)
    db_session.add(user)
    db_session.flush()
    if team is not None:
        db_session.add(TeamMember(team_id=team, user_id=user.id))
        db_session.flush()
    return user


@pytest.fixture
def client_for(db_session: Session) -> Iterator[Callable[[User], TestClient]]:
    def build(user: User) -> TestClient:
        app = FastAPI()

        @app.exception_handler(AutuneError)
        async def render(_: Request, exc: AutuneError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        app.include_router(router, prefix="/api/intelligence")
        app.dependency_overrides[get_session] = lambda: db_session
        app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    yield build


def _meeting(db_session: Session, team: str, title: str, day: int) -> str:
    row = Meeting(team_id=team, title=title, started_at=datetime(2026, 10, day, 5, 0, tzinfo=UTC))
    db_session.add(row)
    db_session.flush()
    return row.id


def _report(db_session: Session, meeting: str, *, draft_id: str = "rdr_a") -> None:
    row = db_session.get(Meeting, meeting)
    assert row is not None
    document = service.meeting_report_document(row, BODY)
    service.save_meeting_report(db_session, meeting, document, draft_id=draft_id)


# --- list ------------------------------------------------------------------------


def test_a_member_lists_the_teams_reports_newest_meeting_first(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    older, newer = (
        _meeting(db_session, team, "기획 회의", 1),
        _meeting(db_session, team, "결제 회의", 2),
    )
    _report(db_session, older)
    _report(db_session, newer)
    service.claim_meeting_report(db_session, older, draft_id="rdr_a")

    response = client_for(_user(db_session, team)).get(f"/api/intelligence/meeting-reports/{team}")

    assert response.status_code == 200
    rows = response.json()
    assert [r["meeting_id"] for r in rows] == [newer, older]
    first, second = rows
    assert first["title"].startswith("결제 회의 · ")
    assert first["body"].startswith(BODY) and "📋" not in first["body"]
    assert (first["status"], second["status"]) == ("draft", "posted")
    assert second["posted_at"] is not None
    assert first["edited_by_name"] is None


def test_another_teams_reports_are_not_listed(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    other = Team(name="Other Team")
    db_session.add(other)
    db_session.flush()
    _report(db_session, _meeting(db_session, other.id, "남의 회의", 1))

    rows = (
        client_for(_user(db_session, team)).get(f"/api/intelligence/meeting-reports/{team}").json()
    )

    assert rows == []


def test_someone_outside_the_team_is_refused(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    _report(db_session, _meeting(db_session, team, "결제 회의", 1))

    response = client_for(_user(db_session, None)).get(f"/api/intelligence/meeting-reports/{team}")

    assert response.status_code == 403


# --- edit ------------------------------------------------------------------------


def test_a_member_edits_a_draft_and_is_recorded_as_its_editor(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting, draft_id="rdr_a")
    editor = _user(db_session, team, name="박재경")

    response = client_for(editor).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )

    assert response.status_code == 200
    assert response.json()["body"] == "✅ 고친 본문"
    assert response.json()["edited_by_name"] == "박재경"
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    assert row.body_markdown.startswith("📋 결제 회의 · ")  # the header stays E's
    assert row.body_markdown.endswith("\n\n✅ 고친 본문")
    assert row.edited_by == editor.id and row.edited_at is not None
    # The approval already queued still names this draft (decided 10/2).
    assert row.draft_id == "rdr_a"


def test_a_posted_report_is_not_edited(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    service.claim_meeting_report(db_session, meeting, draft_id="rdr_a")

    response = client_for(_user(db_session, team)).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )

    assert response.status_code == 409


def test_personal_data_in_an_edit_is_refused_by_category(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)

    response = client_for(_user(db_session, team)).put(
        f"/api/intelligence/meeting-reports/{meeting}",
        json={"body": "연락처 010-1234-5678 로 주세요"},
    )

    assert response.status_code == 422
    assert "010-1234-5678" not in response.text  # categories only, never the text
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and "010-1234-5678" not in row.body_markdown


def test_an_edit_over_the_cap_or_empty_is_refused(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    client = client_for(_user(db_session, team))

    too_long = client.put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "가" * 3000}
    )
    empty = client.put(f"/api/intelligence/meeting-reports/{meeting}", json={"body": "   "})

    assert (too_long.status_code, empty.status_code) == (422, 422)


def test_someone_outside_the_team_cannot_edit(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)

    response = client_for(_user(db_session, None)).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )

    assert response.status_code == 403


def test_deleting_the_editor_keeps_the_report_without_their_id(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    """The editor is a per-person record; it goes with the person, the report stays."""
    meeting = _meeting(db_session, team, "결제 회의", 2)
    _report(db_session, meeting)
    editor = _user(db_session, team, name="박재경")
    client_for(editor).put(
        f"/api/intelligence/meeting-reports/{meeting}", json={"body": "✅ 고친 본문"}
    )

    db_session.execute(sa.delete(TeamMember).where(TeamMember.user_id == editor.id))
    db_session.execute(sa.delete(User).where(User.id == editor.id))
    db_session.expire_all()

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.edited_by is None
