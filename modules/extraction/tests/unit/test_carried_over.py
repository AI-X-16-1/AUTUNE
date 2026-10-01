"""What earlier meetings left open, for the popup a review opens with (WBS 4.8).

SQLite in memory and the router on a bare app. Under test: which items count
as carried over -- open, confirmed, from the same team's earlier meetings --
the order the popup lists them in, and that another team gets nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import AutuneError, Base, Meeting, TeamMember, User, Utterance, get_session
from autune_extraction import service
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem
from autune_extraction.router import router

from .conftest import sign_in

PREFIX = "/api/extraction"
TODAY = date(2026, 9, 30)
NOW = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE", raising=False)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        for meeting_id, team_id, days_ago in (
            ("mtg_old", "team_1", 14),
            ("mtg_last", "team_1", 7),
            ("mtg_now", "team_1", 0),
            ("mtg_later", "team_1", -7),
            ("mtg_other", "team_2", 7),
        ):
            s.add(
                Meeting(
                    id=meeting_id,
                    team_id=team_id,
                    title=f"{meeting_id} 회의",
                    started_at=NOW - timedelta(days=days_ago),
                )
            )
        s.flush()
        yield s


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session)
    yield TestClient(app)


def item(
    session: Session,
    item_id: str,
    meeting_id: str,
    *,
    status: str = "todo",
    due: date | None = None,
) -> None:
    session.add(
        ExtActionItem(
            id=item_id,
            meeting_id=meeting_id,
            description=f"{item_id} 할 일",
            status=status,
            due_date=due,
            confidence=0.9,
            origin="model",
        )
    )
    session.flush()


def test_open_confirmed_items_of_earlier_meetings_carry_over(session: Session) -> None:
    item(session, "act_todo", "mtg_last")
    item(session, "act_doing", "mtg_old", status="in_progress")
    item(session, "act_done", "mtg_last", status="done")
    item(session, "act_draft", "mtg_last", status="needs_confirmation")
    item(session, "act_here", "mtg_now")
    item(session, "act_after", "mtg_later")
    item(session, "act_elsewhere", "mtg_other")

    result = service.carried_over(session, "mtg_now", today=TODAY)

    assert sorted(i.id for i in result.items) == ["act_doing", "act_todo"]
    assert result.open == 2
    assert {i.id: i.meeting_title for i in result.items} == {
        "act_doing": "mtg_old 회의",
        "act_todo": "mtg_last 회의",
    }


def test_overdue_first_then_the_nearest_date_then_undated(session: Session) -> None:
    item(session, "act_undated", "mtg_last")
    item(session, "act_later", "mtg_last", due=TODAY + timedelta(days=9))
    item(session, "act_soon", "mtg_old", due=TODAY + timedelta(days=1))
    item(session, "act_late", "mtg_old", due=TODAY - timedelta(days=3))
    item(session, "act_later_late", "mtg_last", due=TODAY - timedelta(days=1))

    result = service.carried_over(session, "mtg_now", today=TODAY)

    assert [i.id for i in result.items] == [
        "act_late",
        "act_later_late",
        "act_soon",
        "act_later",
        "act_undated",
    ]
    assert result.overdue == 2


def test_it_counts_everything_and_lists_the_most_urgent_ten(session: Session) -> None:
    for n in range(12):
        item(session, f"act_{n:02d}", "mtg_last", due=TODAY + timedelta(days=n))

    result = service.carried_over(session, "mtg_now", today=TODAY)

    assert result.open == 12
    assert [i.id for i in result.items] == [f"act_{n:02d}" for n in range(10)]


def test_the_first_meeting_of_a_team_carries_nothing(session: Session) -> None:
    item(session, "act_later", "mtg_last")

    assert service.carried_over(session, "mtg_old", today=TODAY).open == 0


def test_the_route_answers_a_member(client: TestClient, session: Session) -> None:
    item(session, "act_todo", "mtg_last", due=date(2000, 1, 1))

    response = client.get(f"{PREFIX}/carried-over/mtg_now")

    assert response.status_code == 200
    body = response.json()
    assert (body["open"], body["overdue"]) == (1, 1)
    assert body["items"][0]["id"] == "act_todo"
    assert body["items"][0]["meeting_title"] == "mtg_last 회의"


def test_another_teams_meeting_is_not_found(client: TestClient, session: Session) -> None:
    """Same answer as a meeting that does not exist: a 403 would confirm the id."""
    item(session, "act_elsewhere", "mtg_other")

    assert client.get(f"{PREFIX}/carried-over/mtg_other").status_code == 404
    assert client.get(f"{PREFIX}/carried-over/mtg_missing").status_code == 404
