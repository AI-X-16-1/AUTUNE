"""Listing the meetings a person may see -- ``GET /meetings``, the home screen's data.

The product worked end to end and there was no way to reach any of it without
typing a meeting id into the URL bar: ``/meetings/{id}`` reads one meeting and
nothing listed them. S05 is that list, and the whole of this route is an
authorisation question -- every other read in module A is handed an id and asks
"may you", this one is handed nobody's id and has to answer "which". So the test
that matters most here is ``test_another_teams_meeting_is_absent``: a route that
forgot its join would pass every other test in this file.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio.router import router
from autune_core import AutuneError, Meeting, Team, TeamMember, User, get_session
from autune_core.auth import current_user


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="lister@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


@pytest.fixture
def outsider(db_session: Session) -> User:
    user = User(email="lister-outsider@example.com", display_name="남")
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def app_for(db_session: Session):
    def _build(user: User | None) -> TestClient:
        app = FastAPI()

        @app.exception_handler(AutuneError)
        async def _render(_: Request, exc: AutuneError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        app.include_router(router, prefix="/api/audio")
        app.dependency_overrides[get_session] = lambda: db_session
        if user is not None:
            app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    return _build


@pytest.fixture
def client(app_for, member: User) -> Iterator[TestClient]:
    yield app_for(member)


def _add_meeting(
    session: Session, *, team_id: str, title: str, started_at: datetime | None = None
) -> str:
    row = Meeting(team_id=team_id, title=title, started_at=started_at)
    session.add(row)
    session.flush()
    return row.id


def test_a_member_lists_their_teams_meetings(
    client: TestClient, db_session: Session, team: str, meeting: str
) -> None:
    """Four fields per row and no more: what a list needs to render a link, a
    name, a state and a place in time. No counts -- see ``MeetingSummary``."""
    body = client.get("/api/audio/meetings").json()

    assert body == [
        {
            "meeting_id": meeting,
            "title": "Test Meeting",
            "status": "scheduled",
            "started_at": None,
        }
    ]


def test_the_list_is_newest_first(client: TestClient, db_session: Session, team: str) -> None:
    """Ordered on ``started_at``, so the meeting that happened last reads first.

    Inserted oldest-first, so a route returning insertion order fails this.
    """
    oldest = _add_meeting(
        db_session, team_id=team, title="Oldest", started_at=datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
    )
    middle = _add_meeting(
        db_session, team_id=team, title="Middle", started_at=datetime(2026, 3, 4, 9, 0, tzinfo=UTC)
    )
    newest = _add_meeting(
        db_session, team_id=team, title="Newest", started_at=datetime(2026, 6, 7, 9, 0, tzinfo=UTC)
    )

    body = client.get("/api/audio/meetings").json()

    assert [row["meeting_id"] for row in body] == [newest, middle, oldest]


def test_a_meeting_with_no_start_time_orders_by_when_its_row_was_made(
    client: TestClient, db_session: Session, team: str, meeting: str
) -> None:
    """A recording uploaded after the fact has no ``started_at``, and ordering on
    that column alone would park it at one end of the list whatever happened.

    The ``meeting`` fixture is such a meeting and its row was made just now, so
    it reads above one that started last January -- the ``created_at`` fallback
    ``service.meetings_for`` documents. The field is still null in the payload:
    the list orders on a guess but does not print one.
    """
    last_january = _add_meeting(
        db_session, team_id=team, title="Old", started_at=datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
    )

    body = client.get("/api/audio/meetings").json()

    assert [row["meeting_id"] for row in body] == [meeting, last_january]
    assert body[0]["started_at"] is None


def test_another_teams_meeting_is_absent(
    client: TestClient, db_session: Session, team: str, meeting: str
) -> None:
    """The membership join, pinned. A route that listed every meeting in the
    database would hand one team's meeting titles to another team."""
    other_team = Team(name="Somebody Else's Team")
    db_session.add(other_team)
    db_session.flush()
    theirs = _add_meeting(db_session, team_id=other_team.id, title="Not Mine")

    body = client.get("/api/audio/meetings").json()

    assert [row["meeting_id"] for row in body] == [meeting]
    assert theirs not in [row["meeting_id"] for row in body]


def test_a_member_of_two_teams_sees_both(
    client: TestClient, db_session: Session, team: str, member: User, meeting: str
) -> None:
    """One list across every team the person is on, not one call per team. The
    home screen shows what they can see; a per-team view is S05's later problem."""
    second = Team(name="Second Team")
    db_session.add(second)
    db_session.flush()
    db_session.add(TeamMember(team_id=second.id, user_id=member.id))
    db_session.flush()
    also_mine = _add_meeting(db_session, team_id=second.id, title="Second Team Meeting")

    body = client.get("/api/audio/meetings").json()

    assert {row["meeting_id"] for row in body} == {meeting, also_mine}


def test_a_person_on_no_team_gets_an_empty_list(app_for, outsider: User, meeting: str) -> None:
    """``[]`` and a 200, not a 404 and not an error. A new install has no
    meetings, and the screen says "아직 회의가 없습니다" rather than looking broken."""
    response = app_for(outsider).get("/api/audio/meetings")

    assert response.status_code == 200
    assert response.json() == []


def test_listing_without_a_token_is_refused(app_for, meeting: str) -> None:
    assert app_for(None).get("/api/audio/meetings").status_code == 403
