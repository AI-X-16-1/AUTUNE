"""S20's Google Calendar write (#824): "다음 회의 잡기" puts a meeting's gaps
on an event of the presser's own calendar, and takes them out again.

The routes run on SQLite with the router on a bare app, the harness
``test_read_endpoints`` uses. Google is a fake standing in for
``CalendarClient``: what is under test is which event is written, what is read
and written, what is recorded, and that lines come out again when the gap, the
meeting or the account goes -- not the HTTP client, which
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
from autune_core.errors import PrivacyViolationError
from autune_gap import calendar_writes, service
from autune_gap.models import GapAgendaCleanup, GapAgendaEvent, GapGap
from autune_integrations import IntegrationError, ReconnectRequiredError

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
    """Google, as far as ``calendar_writes`` asks it: the events list, one
    event's fields, and a description written back. Every ``fields`` asked for
    is recorded, so a test can say what was read."""

    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.events = events or []
        self.descriptions: dict[str, str] = {}
        self.attendees: dict[str, list[dict[str, Any]]] = {}
        self.fields: list[str] = []
        self.patched: list[str] = []
        self.fail_with: Exception | None = None

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self.fail_with is not None:
            raise self.fail_with
        params = kwargs.get("params") or {}
        if "fields" in params:
            self.fields.append(params["fields"])
        if path.endswith("/events"):
            low = datetime.fromisoformat(params["timeMin"])
            high = datetime.fromisoformat(params["timeMax"])
            return {
                "items": [
                    e
                    for e in self.events
                    if low <= datetime.fromisoformat(e["start"]["dateTime"]) <= high
                ]
            }
        event_id = path.rsplit("/", 1)[1]
        if method == "PATCH":
            self.patched.append(event_id)
            self.descriptions[event_id] = kwargs["json"]["description"]
            return {}
        found: dict[str, Any] = {"description": self.descriptions.get(event_id, "")}
        if event_id in self.attendees:
            found["attendees"] = self.attendees[event_id]
        return found


def event(event_id: str, start: datetime) -> dict[str, Any]:
    """An event as Google returns it under ``LIST_FIELDS``."""
    return {
        "id": event_id,
        "summary": "주간 회의",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
        "status": "confirmed",
    }


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
    return by_user


@pytest.fixture
def hooks(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """The deletion hooks and the drain open their own ``session_scope``;
    here it is the test's session, and the Google client counts as configured."""

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.flush()

    monkeypatch.setattr(calendar_writes, "session_scope", scope)
    monkeypatch.setattr(calendar_writes, "_client_configured", lambda: True)
    return session


@pytest.fixture
def teammate(session: Session) -> str:
    session.add(User(id=TEAMMATE, email="Mate@Example.com", display_name="가나다"))
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


def recorded(session: Session) -> set[tuple[str, str, str]]:
    return {(r.gap_id, r.user_id, r.event_id) for r in session.scalars(select(GapAgendaEvent))}


# --- the agenda line -----------------------------------------------------------


def test_a_line_is_added_once_and_taken_out_by_its_gap_id() -> None:
    line = f"{calendar_writes.AGENDA_PREFIX} 제목 — 질문 (gap_1)"

    added = calendar_writes.with_line("기존 안건", line, "gap_1")

    assert added == f"기존 안건\n{line}"
    assert calendar_writes.with_line(added, line, "gap_1") is None
    assert calendar_writes.without_lines(added, ["gap_1"]) == "기존 안건"
    assert calendar_writes.without_lines("기존 안건", ["gap_1"]) is None


def test_another_gaps_line_is_left_alone() -> None:
    other = f"{calendar_writes.AGENDA_PREFIX} 다른 갭 (gap_2)"

    assert calendar_writes.without_lines(other, ["gap_1"]) is None


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


# --- "다음 회의 잡기" -----------------------------------------------------------


def test_scheduling_sends_every_open_gap_onto_the_next_meetings_event(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    gap(session, "gap_high", risk_score=0.9)
    gap(session, "gap_second", risk_score=0.8)
    gap(session, "gap_dismissed", dismissed=True)
    session.get(GapGap, gap(session, "gap_low", risk_score=0.1)).severity = "low"
    session.flush()
    next_meeting(session)
    google = calendars[MEMBER] = FakeCalendar([event("evt_meeting", STARTS)])

    first = client.post(f"{PREFIX}/agenda/{MEETING}")
    again = client.post(f"{PREFIX}/agenda/{MEETING}")

    assert first.json() == {"meeting_id": MEETING, "carried": 2, "calendar": "added"}
    assert again.json()["calendar"] == "added"
    lines = google.descriptions["evt_meeting"].splitlines()
    assert [line.rsplit(" ", 1)[1] for line in lines] == ["(gap_high)", "(gap_second)"]
    marked = {g.id for g in session.scalars(select(GapGap)) if g.carried_at is not None}
    assert marked == {"gap_high", "gap_second"}


def test_scheduling_with_no_open_gap_writes_nothing(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    gap(session, "gap_dismissed", dismissed=True)
    next_meeting(session)
    google = calendars[MEMBER] = FakeCalendar([event("evt_meeting", STARTS)])

    response = client.post(f"{PREFIX}/agenda/{MEETING}")

    assert response.json() == {"meeting_id": MEETING, "carried": 0, "calendar": "not_tried"}
    assert google.descriptions == {}


def test_scheduling_without_a_next_meeting_still_sends_the_gaps(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    gap(session, "gap_1")

    response = client.post(f"{PREFIX}/agenda/{MEETING}")

    assert response.json()["calendar"] == "no_next_meeting"
    assert session.get(GapGap, "gap_1").carried_at is not None


def test_another_teams_meeting_cannot_be_scheduled_from(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    gap(session, "gap_foreign", meeting_id="mtg_elsewhere")

    assert client.post(f"{PREFIX}/agenda/mtg_elsewhere").status_code == 404
    assert session.get(GapGap, "gap_foreign").carried_at is None


def test_the_picker_lists_my_upcoming_events(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    calendars[MEMBER] = FakeCalendar(
        [event("evt_soon", STARTS), event("evt_far", STARTS + timedelta(days=30))]
    )

    body = client.get(f"{PREFIX}/agenda/{MEETING}/events").json()

    assert body["calendar"] == "ok"
    assert [e["id"] for e in body["events"]] == ["evt_soon"]
    assert body["events"][0]["summary"] == "주간 회의"


def test_the_picker_says_when_no_calendar_is_connected(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    assert client.get(f"{PREFIX}/agenda/{MEETING}/events").json() == {
        "calendar": "not_connected",
        "events": [],
    }


def test_the_picker_is_for_members_of_the_meetings_team(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    assert client.get(f"{PREFIX}/agenda/mtg_elsewhere/events").status_code == 404


def test_scheduling_onto_a_picked_event_needs_no_scheduled_meeting(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    gap(session, "gap_1")
    google = calendars[MEMBER] = FakeCalendar([event("evt_picked", STARTS)])

    response = client.post(f"{PREFIX}/agenda/{MEETING}", json={"event_id": "evt_picked"})

    assert response.json() == {"meeting_id": MEETING, "carried": 1, "calendar": "added"}
    assert google.descriptions["evt_picked"].endswith("(gap_1)")


# --- what is read (mkkim68 on #824) ---------------------------------------------


def test_the_picker_and_the_write_ask_google_for_named_fields_only(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    gap(session, "gap_1")
    google = calendars[MEMBER] = FakeCalendar([event("evt_picked", STARTS)])

    client.get(f"{PREFIX}/agenda/{MEETING}/events")
    client.post(f"{PREFIX}/agenda/{MEETING}", json={"event_id": "evt_picked"})

    assert google.fields == [calendar_writes.LIST_FIELDS, calendar_writes.WRITE_FIELDS]
    assert "description" not in calendar_writes.LIST_FIELDS
    assert "attendees" not in calendar_writes.LIST_FIELDS


# --- an event shared outside the team (mkkim68 on #824) -------------------------


def test_an_event_with_someone_outside_the_team_is_not_written(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    gap(session, "gap_1")
    google = calendars[MEMBER] = FakeCalendar([event("evt_picked", STARTS)])
    google.attendees["evt_picked"] = [
        {"email": f"{MEMBER}@example.com", "self": True},
        {"email": "guest@partner.example"},
    ]

    response = client.post(f"{PREFIX}/agenda/{MEETING}", json={"event_id": "evt_picked"})

    assert response.json()["calendar"] == "external_attendees"
    assert google.patched == []
    assert recorded(session) == set()
    # The mark is the team's own and is set either way.
    assert session.get(GapGap, "gap_1").carried_at is not None


def test_teammates_rooms_and_the_owner_do_not_count_as_outside(
    client: TestClient, session: Session, calendars: dict[str, Any], teammate: str
) -> None:
    gap(session, "gap_1")
    google = calendars[MEMBER] = FakeCalendar([event("evt_picked", STARTS)])
    google.attendees["evt_picked"] = [
        {"email": f"{MEMBER}@example.com", "self": True},
        {"email": "mate@example.com"},  # the teammate, in another case
        {"email": "room-3f@resource.calendar.google.com", "resource": True},
    ]

    response = client.post(f"{PREFIX}/agenda/{MEETING}", json={"event_id": "evt_picked"})

    assert response.json()["calendar"] == "added"
    assert google.descriptions["evt_picked"].endswith("(gap_1)")


def test_a_line_comes_out_even_after_an_outsider_was_invited(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    gap(session, "gap_1")
    next_meeting(session)
    google = calendars[MEMBER] = FakeCalendar([event("evt_meeting", STARTS)])
    client.post(f"{PREFIX}/gaps/gap_1/carry")
    google.attendees["evt_meeting"] = [{"email": "guest@partner.example"}]

    response = client.delete(f"{PREFIX}/gaps/gap_1/carry")

    assert response.json()["calendar"] == "removed"
    assert google.descriptions["evt_meeting"] == ""


# --- what is recorded, and taken out when it goes -------------------------------


def test_a_line_is_recorded_while_it_is_there(
    client: TestClient, session: Session, calendars: dict[str, Any]
) -> None:
    gap(session, "gap_1")
    next_meeting(session)
    calendars[MEMBER] = FakeCalendar([event("evt_meeting", STARTS)])

    client.post(f"{PREFIX}/gaps/gap_1/carry")
    client.post(f"{PREFIX}/gaps/gap_1/carry")
    assert recorded(session) == {("gap_1", MEMBER, "evt_meeting")}

    client.delete(f"{PREFIX}/gaps/gap_1/carry")
    assert recorded(session) == set()


def test_a_deleted_meetings_lines_are_queued_and_then_taken_out(
    client: TestClient, session: Session, calendars: dict[str, Any], hooks: Session
) -> None:
    gap(session, "gap_1")
    gap(session, "gap_2", risk_score=0.8)
    google = calendars[MEMBER] = FakeCalendar([event("evt_picked", STARTS)])
    google.descriptions["evt_picked"] = "1. 지난주 회고"
    client.post(f"{PREFIX}/agenda/{MEETING}", json={"event_id": "evt_picked"})

    calendar_writes.queue_meeting_agenda_lines(MEETING)
    calendar_writes.queue_meeting_agenda_lines(MEETING)  # a hook may run twice
    queued_lines = list(session.scalars(select(GapAgendaCleanup)))
    assert {q.gap_id for q in queued_lines} == {"gap_1", "gap_2"}
    assert len(queued_lines) == 2

    assert calendar_writes.drain_agenda_cleanup() == 1
    assert google.descriptions["evt_picked"] == "1. 지난주 회고"
    assert list(session.scalars(select(GapAgendaCleanup))) == []


def test_an_event_already_gone_counts_as_cleaned(
    session: Session, calendars: dict[str, Any], hooks: Session
) -> None:
    google = calendars[MEMBER] = FakeCalendar()
    google.fail_with = calendar_writes.PermanentIntegrationError("gone", upstream_status=410)
    session.add(
        GapAgendaCleanup(user_id=MEMBER, calendar_id="primary", event_id="evt_x", gap_id="gap_1")
    )
    session.flush()

    assert calendar_writes.drain_agenda_cleanup() == 1
    assert list(session.scalars(select(GapAgendaCleanup))) == []


def test_a_transient_failure_is_tried_again_and_then_given_up(
    session: Session, calendars: dict[str, Any], hooks: Session
) -> None:
    google = calendars[MEMBER] = FakeCalendar()
    google.fail_with = IntegrationError("unreachable")
    session.add(
        GapAgendaCleanup(user_id=MEMBER, calendar_id="primary", event_id="evt_x", gap_id="gap_1")
    )
    session.flush()

    for _ in range(calendar_writes.CLEANUP_MAX_ATTEMPTS - 1):
        calendar_writes.drain_agenda_cleanup()
    assert session.scalar(select(GapAgendaCleanup.attempts)) == (
        calendar_writes.CLEANUP_MAX_ATTEMPTS - 1
    )

    calendar_writes.drain_agenda_cleanup()
    assert list(session.scalars(select(GapAgendaCleanup))) == []


def test_a_queued_line_whose_owner_has_no_calendar_is_dropped(
    session: Session, calendars: dict[str, Any], hooks: Session
) -> None:
    session.add(
        GapAgendaCleanup(user_id=MEMBER, calendar_id="primary", event_id="evt_x", gap_id="gap_1")
    )
    session.flush()

    assert calendar_writes.drain_agenda_cleanup() == 0
    assert list(session.scalars(select(GapAgendaCleanup))) == []


def test_an_account_takes_its_lines_out_before_it_goes(
    client: TestClient, session: Session, calendars: dict[str, Any], hooks: Session
) -> None:
    gap(session, "gap_1")
    google = calendars[MEMBER] = FakeCalendar([event("evt_picked", STARTS)])
    google.descriptions["evt_picked"] = "안건"
    client.post(f"{PREFIX}/agenda/{MEETING}", json={"event_id": "evt_picked"})
    google.descriptions["evt_old"] = f"{calendar_writes.AGENDA_PREFIX} 지난 갭 (gap_old)"
    session.add(
        GapAgendaCleanup(
            user_id=MEMBER, calendar_id="primary", event_id="evt_old", gap_id="gap_old"
        )
    )
    session.flush()

    calendar_writes.forget_user_agenda_lines(MEMBER)

    assert google.descriptions == {"evt_picked": "안건", "evt_old": ""}
    assert recorded(session) == set()
    assert list(session.scalars(select(GapAgendaCleanup))) == []


def test_an_account_deletion_goes_on_when_google_refuses(
    client: TestClient, session: Session, calendars: dict[str, Any], hooks: Session
) -> None:
    gap(session, "gap_1")
    calendars[MEMBER] = FakeCalendar([event("evt_picked", STARTS)])
    client.post(f"{PREFIX}/agenda/{MEETING}", json={"event_id": "evt_picked"})
    calendars[MEMBER] = ReconnectRequiredError("revoked")

    calendar_writes.forget_user_agenda_lines(MEMBER)  # does not raise

    assert recorded(session) == set()


def test_a_question_whose_words_were_deleted_has_its_line_taken_out(
    client: TestClient, session: Session, calendars: dict[str, Any], hooks: Session
) -> None:
    """#587: the line quotes the old question, so it comes out; the mark stays."""
    questioned(session, gap(session, "gap_1"))
    gap(session, "gap_2", risk_score=0.8)
    google = calendars[MEMBER] = FakeCalendar([event("evt_picked", STARTS)])
    client.post(f"{PREFIX}/agenda/{MEETING}", json={"event_id": "evt_picked"})

    assert calendar_writes.queue_gap_lines(session, ["gap_1"]) == 1
    calendar_writes.drain_agenda_cleanup()

    assert google.descriptions["evt_picked"].endswith("(gap_2)")
    assert "(gap_1)" not in google.descriptions["evt_picked"]
    assert recorded(session) == {("gap_2", MEMBER, "evt_picked")}
    assert session.get(GapGap, "gap_1").carried_at is not None


def test_both_hooks_are_registered() -> None:
    from autune_core.deletion import registered_modules

    meeting_hooks, user_hooks = registered_modules()
    assert "gap" in meeting_hooks
    assert "gap" in user_hooks
