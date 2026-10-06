"""S20's Google Calendar writes (#824): the next meeting's agenda line and a
question on one teammate's calendar.

The routes run on SQLite with the router on a bare app, the harness
``test_read_endpoints`` uses. Google is a fake standing in for
``CalendarClient``: what is under test is which event is written, what is
written to it, and what is stored, not the HTTP client, which
``packages/integrations`` tests.
"""

# ruff: noqa: F401, F811  -- fixtures shared with test_read_endpoints

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_core import Meeting, TeamMember, User
from autune_gap import calendar_writes, service
from autune_gap.models import GapGap, GapQuestion
from autune_integrations import CalendarEvent, ReconnectRequiredError

from .test_read_endpoints import (
    MEETING,
    MEMBER,
    OUTSIDER,
    PREFIX,
    TEAM,
    anonymous,
    client,
    gap,
    queued,
    session,
)

TEAMMATE = "usr_teammate"
STARTS = datetime.now(UTC).replace(microsecond=0) + timedelta(days=2)


class FakeCalendar:
    """The calls ``calendar_writes`` makes, recorded."""

    def __init__(self, events: list[CalendarEvent] | None = None) -> None:
        self.events = events or []
        self.descriptions: dict[str, str] = {}
        self.created: list[dict[str, Any]] = []

    def list_events(
        self, calendar_id: str, time_min: datetime, time_max: datetime, *, limit: int = 50
    ) -> list[CalendarEvent]:
        return [
            e
            for e in self.events
            if isinstance(e.start, datetime) and time_min <= e.start <= time_max
        ]

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        event_id = path.rsplit("/", 1)[1]
        if method == "PATCH":
            self.descriptions[event_id] = kwargs["json"]["description"]
        return {"description": self.descriptions.get(event_id, "")}

    def create_all_day_event(
        self,
        calendar_id: str,
        summary: str,
        day: date,
        *,
        description: str = "",
        private: dict[str, str] | None = None,
    ) -> str:
        self.created.append(
            {"summary": summary, "day": day, "description": description, "private": private}
        )
        return f"evt_{len(self.created)}"


def event(event_id: str, start: datetime) -> CalendarEvent:
    return CalendarEvent(
        id=event_id,
        summary="주간 회의",
        start=start,
        end=start + timedelta(hours=1),
        private={},
        cancelled=False,
    )


@pytest.fixture
def calendars(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Each person's calendar, by user id. A person missing from it has not
    connected one; one mapped to an exception has a grant Google refuses."""
    by_user: dict[str, Any] = {}

    @contextmanager
    def calendar_of(_session: Session, user_id: str) -> Iterator[tuple[Any, str] | None]:
        found = by_user.get(user_id)
        if isinstance(found, Exception):
            raise found
        yield (found, "primary") if found is not None else None

    monkeypatch.setattr(calendar_writes, "calendar_of", calendar_of)
    monkeypatch.setattr(service, "users_with_integration", lambda _session, _name: list(by_user))
    return by_user


@pytest.fixture
def teammate(session: Session) -> str:
    session.add(User(id=TEAMMATE, email="mate@example.com", display_name="가나다"))
    session.add(TeamMember(team_id=TEAM, user_id=TEAMMATE))
    session.flush()
    return TEAMMATE


def next_meeting(session: Session, starts: datetime = STARTS) -> str:
    session.add(Meeting(id="mtg_next", team_id=TEAM, title="다음 회의", started_at=starts))
    session.flush()
    return "mtg_next"


def questioned(session: Session, gap_id: str) -> GapGap:
    row = session.get(GapGap, gap_id)
    assert row is not None
    row.suggested_question = "목표 응답 시간을 누가 정합니까?"
    session.flush()
    return row


# --- the agenda line -----------------------------------------------------------


def test_a_line_is_added_once_and_taken_out_by_its_gap_id() -> None:
    line = f"{calendar_writes.AGENDA_PREFIX} 제목 — 질문 (gap_1)"

    added = calendar_writes.with_line("기존 안건", line, "gap_1")

    assert added == f"기존 안건\n{line}"
    assert calendar_writes.with_line(added, line, "gap_1") is None
    assert calendar_writes.without_line(added, "gap_1") == "기존 안건"
    assert calendar_writes.without_line("기존 안건", "gap_1") is None


def test_another_gaps_line_is_left_alone() -> None:
    other = f"{calendar_writes.AGENDA_PREFIX} 다른 갭 (gap_2)"

    assert calendar_writes.without_line(other, "gap_1") is None


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 10, 6), date(2026, 10, 7)),  # Tuesday -> Wednesday
        (date(2026, 10, 9), date(2026, 10, 12)),  # Friday -> Monday
        (date(2026, 10, 10), date(2026, 10, 12)),  # Saturday -> Monday
    ],
)
def test_a_question_defaults_to_the_next_working_day(today: date, expected: date) -> None:
    assert calendar_writes.next_working_day(today) == expected


# --- "다음 회의 어젠다로" -------------------------------------------------------


def test_carrying_writes_the_gap_onto_the_next_meetings_event(
    client: TestClient,
    session: Session,
    calendars: dict[str, Any],
) -> None:
    questioned(session, gap(session, "gap_1"))
    next_meeting(session)
    google = calendars[MEMBER] = FakeCalendar(
        [event("evt_other", STARTS + timedelta(hours=3)), event("evt_meeting", STARTS)]
    )
    google.descriptions["evt_meeting"] = "1. 지난주 회고"

    response = client.post(f"{PREFIX}/gaps/gap_1/carry")

    assert response.json()["calendar"] == "added"
    assert response.json()["carried"] is True
    assert google.descriptions["evt_meeting"].splitlines() == [
        "1. 지난주 회고",
        f"{calendar_writes.AGENDA_PREFIX} 성능 요구사항이 정해지지 않았습니다"
        " — 목표 응답 시간을 누가 정합니까? (gap_1)",
    ]
    assert "evt_other" not in google.descriptions


def test_taking_it_back_takes_the_line_out(
    client: TestClient,
    session: Session,
    calendars: dict[str, Any],
) -> None:
    gap(session, "gap_1")
    next_meeting(session)
    google = calendars[MEMBER] = FakeCalendar([event("evt_meeting", STARTS)])
    google.descriptions["evt_meeting"] = "1. 지난주 회고"
    client.post(f"{PREFIX}/gaps/gap_1/carry")

    response = client.delete(f"{PREFIX}/gaps/gap_1/carry")

    assert response.json()["calendar"] == "removed"
    assert google.descriptions["evt_meeting"] == "1. 지난주 회고"


def test_a_meeting_already_held_is_not_the_next_one(
    client: TestClient,
    session: Session,
    calendars: dict[str, Any],
) -> None:
    gap(session, "gap_1")
    next_meeting(session, datetime.now(UTC) - timedelta(hours=1))
    calendars[MEMBER] = FakeCalendar()

    assert client.post(f"{PREFIX}/gaps/gap_1/carry").json()["calendar"] == "no_next_meeting"


@pytest.mark.parametrize(
    ("google", "outcome"),
    [
        (None, "not_connected"),
        (FakeCalendar(), "no_event"),
        (ReconnectRequiredError("refused"), "reconnect_required"),
    ],
)
def test_a_calendar_that_cannot_take_the_line_still_leaves_the_mark(
    client: TestClient,
    session: Session,
    calendars: dict[str, Any],
    google: Any,
    outcome: str,
) -> None:
    gap(session, "gap_1")
    next_meeting(session)
    if google is not None:
        calendars[MEMBER] = google

    response = client.post(f"{PREFIX}/gaps/gap_1/carry")

    assert response.status_code == 200
    assert response.json()["calendar"] == outcome
    assert session.get(GapGap, "gap_1").carried_at is not None


# --- "담당자 지정해 질문" ------------------------------------------------------


def test_the_picker_lists_the_team_with_calendars_and_who_was_asked(
    client: TestClient,
    session: Session,
    calendars: dict[str, Any],
    teammate: str,
) -> None:
    gap(session, "gap_1")
    calendars[teammate] = FakeCalendar()
    client.post(f"{PREFIX}/gaps/gap_1/ask", json={"user_id": teammate})

    members = client.get(f"{PREFIX}/gaps/gap_1/ask").json()["members"]

    assert members == [
        {"user_id": MEMBER, "name": MEMBER, "calendar_connected": False, "asked": False},
        {"user_id": teammate, "name": "가나다", "calendar_connected": True, "asked": True},
    ]


def test_asking_puts_the_question_on_their_calendar_once(
    client: TestClient,
    session: Session,
    calendars: dict[str, Any],
    teammate: str,
) -> None:
    questioned(session, gap(session, "gap_1"))
    google = calendars[teammate] = FakeCalendar()

    first = client.post(f"{PREFIX}/gaps/gap_1/ask", json={"user_id": teammate, "day": "2026-10-08"})
    second = client.post(f"{PREFIX}/gaps/gap_1/ask", json={"user_id": teammate})

    assert first.json() == {"gap_id": "gap_1", "user_id": teammate, "outcome": "added"}
    assert second.json()["outcome"] == "already_asked"
    assert google.created == [
        {
            "summary": f"{calendar_writes.QUESTION_PREFIX} 성능 요구사항이 정해지지 않았습니다",
            "day": date(2026, 10, 8),
            "description": f"목표 응답 시간을 누가 정합니까?\n\n{calendar_writes.QUESTION_FOOTER}",
            "private": {"autune": "1", "autune_gap": "gap_1"},
        }
    ]
    stored = session.scalars(select(GapQuestion)).all()
    assert [(q.gap_id, q.user_id, q.event_id) for q in stored] == [("gap_1", teammate, "evt_1")]


def test_a_person_without_a_calendar_is_told_and_nothing_is_stored(
    client: TestClient,
    session: Session,
    calendars: dict[str, Any],
    teammate: str,
) -> None:
    gap(session, "gap_1")

    response = client.post(f"{PREFIX}/gaps/gap_1/ask", json={"user_id": teammate})

    assert response.json()["outcome"] == "not_connected"
    assert session.scalars(select(GapQuestion)).all() == []


def test_only_someone_on_the_meetings_team_can_be_asked(
    client: TestClient,
    session: Session,
    calendars: dict[str, Any],
) -> None:
    gap(session, "gap_1")
    calendars[OUTSIDER] = FakeCalendar()

    response = client.post(f"{PREFIX}/gaps/gap_1/ask", json={"user_id": OUTSIDER})

    assert response.status_code == 422
    assert calendars[OUTSIDER].created == []


def test_another_teams_gap_cannot_be_asked_about(
    client: TestClient,
    session: Session,
    calendars: dict[str, Any],
) -> None:
    gap(session, "gap_foreign", meeting_id="mtg_elsewhere")

    assert client.get(f"{PREFIX}/gaps/gap_foreign/ask").status_code == 404
    assert (
        client.post(f"{PREFIX}/gaps/gap_foreign/ask", json={"user_id": MEMBER}).status_code == 404
    )


def test_asking_needs_a_caller(anonymous: TestClient, session: Session) -> None:
    gap(session, "gap_1")

    assert anonymous.post(f"{PREFIX}/gaps/gap_1/ask", json={"user_id": MEMBER}).status_code == 403
