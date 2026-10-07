"""A person can turn their own due-date reminders off (review of #751).

On unless they did: a row in ``ext_due_reminder_optouts`` means off. Only the
caller's own setting is read or changed -- the route has no parameter naming
anybody else -- and the reminders skip a person who turned them off, both when
the list is made and again at the moment of sending.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import AutuneError, Base, Meeting, TeamMember, User, get_session
from autune_extraction import router as router_module
from autune_extraction import service
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem, ExtDueReminderOptOut
from autune_extraction.router import router
from autune_integrations.fakes import FakeSlack

from .conftest import READER, sign_in

PREFIX = "/api/extraction"
MEETING = "mtg_1"
TEAM = "team_1"
NOW = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)  # 10:00 in Korea
TOMORROW = date(2026, 10, 3)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id=TEAM, title="주간 회의"))
        s.add(User(id=READER, email=f"{READER}@example.com", display_name="읽는 사람"))
        s.flush()
        yield s


def client_for(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sent: bool,
    weekly: bool = False,
    daily: bool = False,
) -> TestClient:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session)
    monkeypatch.setattr(
        router_module,
        "get_settings",
        lambda: ExtractionSettings(  # type: ignore[call-arg]
            _env_file=None, due_reminders=sent, weekly_digest=weekly, daily_digest=daily
        ),
    )
    return TestClient(app)


def test_they_are_on_unless_the_person_turned_them_off(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_for(session, monkeypatch, sent=True)
    assert client.get(f"{PREFIX}/me/due-reminders").json() == {
        "on": True,
        "sent_here": True,
        "weekly_here": False,
        "daily_here": False,
        "work_report_here": False,
    }


def test_turning_them_off_and_on_again(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    client = client_for(session, monkeypatch, sent=True)

    off = client.put(f"{PREFIX}/me/due-reminders", json={"on": False})
    assert off.json() == {
        "on": False,
        "sent_here": True,
        "weekly_here": False,
        "daily_here": False,
        "work_report_here": False,
    }
    assert session.get(ExtDueReminderOptOut, READER) is not None
    assert client.get(f"{PREFIX}/me/due-reminders").json()["on"] is False

    client.put(f"{PREFIX}/me/due-reminders", json={"on": False})  # twice is harmless
    on = client.put(f"{PREFIX}/me/due-reminders", json={"on": True})
    assert on.json()["on"] is True
    assert session.get(ExtDueReminderOptOut, READER) is None


def test_the_screen_is_told_when_this_deployment_sends_none(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_for(session, monkeypatch, sent=False)
    assert client.get(f"{PREFIX}/me/due-reminders").json() == {
        "on": True,
        "sent_here": False,
        "weekly_here": False,
        "daily_here": False,
        "work_report_here": False,
    }


def test_the_screen_is_told_which_of_the_three_this_deployment_sends(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dev, 2026-10-05: reminders off, both digests on -- and the one flag
    had the screen say the server sent none."""
    client = client_for(session, monkeypatch, sent=False, weekly=True, daily=True)

    assert client.get(f"{PREFIX}/me/due-reminders").json() == {
        "on": True,
        "sent_here": False,
        "weekly_here": True,
        "daily_here": True,
        "work_report_here": False,
    }
    answer = client.put(f"{PREFIX}/me/due-reminders", json={"on": False}).json()
    assert (answer["weekly_here"], answer["daily_here"]) == (True, True)


def test_nobody_else_can_be_named(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The body takes ``on`` and nothing else; a user id is refused, not used."""
    client = client_for(session, monkeypatch, sent=True)

    response = client.put(
        f"{PREFIX}/me/due-reminders", json={"on": False, "user_id": "user_someone_else"}
    )

    assert response.status_code == 422
    assert session.query(ExtDueReminderOptOut).count() == 0


def test_the_row_goes_with_the_account() -> None:
    fk = next(iter(ExtDueReminderOptOut.__table__.c.user_id.foreign_keys))
    assert fk.column.table.name == "users"
    assert fk.ondelete == "CASCADE"
    assert {c.name for c in ExtDueReminderOptOut.__table__.columns} == {"user_id", "created_at"}


# --- the reminders read it ---------------------------------------------------------------


def owed_item(session: Session) -> None:
    session.add(TeamMember(team_id=TEAM, user_id=READER))
    session.add(
        ExtActionItem(
            id="act_1",
            meeting_id=MEETING,
            description="스펙 초안 공유",
            status="todo",
            assignee_id=READER,
            due_date=TOMORROW,
            confidence=0.9,
            origin="model",
        )
    )
    session.flush()


def test_a_person_who_turned_them_off_is_owed_none(session: Session) -> None:
    owed_item(session)
    assert [r.action_item_id for r in service.due_reminders_to_send(session, now=NOW)] == ["act_1"]

    service.set_due_reminders(session, READER, on=False, now=NOW)

    assert service.due_reminders_to_send(session, now=NOW) == []


def test_turning_them_off_after_the_list_was_made_still_stops_the_message(
    session: Session,
) -> None:
    owed_item(session)
    (reminder,) = service.due_reminders_to_send(session, now=NOW)
    service.set_due_reminders(session, READER, on=False, now=NOW)
    slack = FakeSlack()

    assert service.send_due_reminder(session, slack, reminder, now=NOW) is False
    assert slack.sent == []
