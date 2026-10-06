"""A person's own leave dates on their own calendar, when they tick the box (2026-10-06).

The rule this guards (privacy.md section 6): when someone is away is theirs
alone. The dates reach Google only by that person's own tick, as one private
event on their own calendar, and Autune keeps its id only so the same event can
be moved or removed.

SQLite in memory, a fake of Google Calendar's events endpoint, and the real
router for the two routes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import AutuneError, Base, Team, TeamMember, User, get_session
from autune_core.errors import ConflictError
from autune_core.user_integrations import UserIntegrationConfig
from autune_extraction import leave_calendar, reminders, service, tasks
from autune_extraction.models import ExtCalendarCleanup, ExtNotificationPause
from autune_extraction.router import router
from autune_integrations import (
    PermanentIntegrationError,
    ReconnectRequiredError,
    TransientIntegrationError,
)

from .conftest import READER, sign_in

PREFIX = "/api/extraction"
NOW = datetime.now(tz=UTC)
TODAY = reminders.korean_day(NOW)
FIRST, LAST = TODAY + timedelta(days=2), TODAY + timedelta(days=6)
LATER_FIRST, LATER_LAST = TODAY + timedelta(days=9), TODAY + timedelta(days=10)


class FakeCalendar:
    """Google Calendar's events endpoint, as ``leave_calendar`` calls it."""

    def __init__(self) -> None:
        self.bodies: dict[str, dict[str, Any]] = {}
        self.posted: list[dict[str, Any]] = []
        self.patched: list[str] = []
        self.deleted: list[tuple[str, str]] = []
        self.gone: set[str] = set()
        self.fail: Exception | None = None

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        if self.fail is not None:
            raise self.fail
        if method == "POST":
            event_id = f"evt_{len(self.posted) + 1}"
            self.posted.append({"calendar": path.split("/")[2], **json})
            self.bodies[event_id] = json
            return {"id": event_id}
        event_id = path.rsplit("/", 1)[1]
        if event_id in self.gone:
            return {"status": "cancelled"}  # Google keeps a deleted event a while
        self.patched.append(event_id)
        self.bodies[event_id] = {**self.bodies.get(event_id, {}), **json}
        return {"status": "confirmed"}

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.deleted.append((calendar_id, event_id))
        self.bodies.pop(event_id, None)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (User, Team, TeamMember)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        for user in (READER, "user_lee"):
            s.add(User(id=user, email=f"{user}@example.com", display_name=user))
        s.commit()
        yield s


@pytest.fixture
def calendar() -> FakeCalendar:
    return FakeCalendar()


@pytest.fixture
def mine(calendar: FakeCalendar) -> Any:
    """``calendar_for``: the reader's own calendar, and nobody else's."""
    asked: list[str] = []

    def calendar_for(user_id: str) -> tuple[Any, str] | None:
        asked.append(user_id)
        return (calendar, "primary") if user_id == READER else None

    calendar_for.asked = asked  # type: ignore[attr-defined]
    return calendar_for


def save(
    session: Session,
    calendar_for: Any,
    first: date | None = FIRST,
    last: date | None = LAST,
    *,
    on_calendar: bool = True,
    user_id: str = READER,
) -> str:
    return leave_calendar.set_leave(
        session,
        calendar_for,
        user_id,
        starts_on=first,
        ends_on=last,
        on_calendar=on_calendar,
        now=NOW,
    )


def pause(session: Session, user_id: str = READER) -> ExtNotificationPause | None:
    """The row as the route leaves it: committed, then read again."""
    session.commit()
    session.expire_all()
    return session.get(ExtNotificationPause, user_id)


# --- nothing without the tick ------------------------------------------------


def test_dates_saved_without_the_tick_reach_no_calendar(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    assert save(session, mine, on_calendar=False) == "off"

    row = pause(session)
    assert row is not None and (row.starts_on, row.ends_on) == (FIRST, LAST)
    assert row.calendar_event_id is None
    assert calendar.posted == [] and calendar.patched == [] and calendar.deleted == []
    assert mine.asked == [], "the person's grant is not even looked up"


# --- the tick ----------------------------------------------------------------


def test_the_tick_puts_one_private_all_day_event_over_the_range(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    assert save(session, mine) == "added"

    (event,) = calendar.posted
    assert event == {
        "calendar": "primary",
        "summary": "휴가",
        "description": leave_calendar.EVENT_DESCRIPTION,
        "start": {"date": FIRST.isoformat()},
        # Google's all-day end is exclusive: through LAST ends the day after.
        "end": {"date": (LAST + timedelta(days=1)).isoformat()},
        "visibility": "private",
        "transparency": "opaque",
        "extendedProperties": {"private": {"autune_leave": "1"}},
    }, "the two dates and fixed words: no attendee, no meeting, no item, nobody's name"
    row = pause(session)
    assert row is not None and row.calendar_event_id == "evt_1"
    assert mine.asked == [READER], "their own calendar, through their own grant"


def test_the_event_is_not_one_the_due_date_read_back_asks_for() -> None:
    """``pull_calendar_changes`` asks Google for ``calendar_sync.TAG``; a leave
    event carrying it would come back on every poll."""
    from autune_extraction import calendar_sync, project_send

    assert leave_calendar.LEAVE_TAG[0] not in (calendar_sync.TAG[0], project_send.MINUTES_TAG[0])


def test_a_changed_range_moves_the_same_event(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    save(session, mine)

    assert save(session, mine, LATER_FIRST, LATER_LAST) == "added"

    assert len(calendar.posted) == 1 and calendar.patched == ["evt_1"]
    assert calendar.bodies["evt_1"]["start"] == {"date": LATER_FIRST.isoformat()}
    assert calendar.bodies["evt_1"]["end"] == {"date": (LATER_LAST + timedelta(days=1)).isoformat()}
    assert calendar.bodies["evt_1"]["visibility"] == "private"
    row = pause(session)
    assert row is not None and row.calendar_event_id == "evt_1"
    assert (row.starts_on, row.ends_on) == (LATER_FIRST, LATER_LAST)
    assert session.query(ExtNotificationPause).count() == 1, "one range a person"


def test_an_event_deleted_by_hand_is_made_again_not_revived(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    save(session, mine)
    calendar.gone.add("evt_1")

    assert save(session, mine) == "added"

    assert len(calendar.posted) == 2 and calendar.patched == []
    row = pause(session)
    assert row is not None and row.calendar_event_id == "evt_2"


# --- taking it back ----------------------------------------------------------


def test_saving_without_the_tick_removes_the_event_and_keeps_the_dates(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    save(session, mine)

    assert save(session, mine, on_calendar=False) == "removed"

    assert calendar.deleted == [("primary", "evt_1")]
    row = pause(session)
    assert row is not None and row.calendar_event_id is None
    assert (row.starts_on, row.ends_on) == (FIRST, LAST)


def test_clearing_the_dates_removes_the_event(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    save(session, mine)

    assert save(session, mine, None, None, on_calendar=False) == "removed"

    assert calendar.deleted == [("primary", "evt_1")]
    assert pause(session) is None


def test_clearing_removes_the_event_whatever_the_box_says(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    """No dates, nothing to have on a calendar: a tick sent with them is moot."""
    save(session, mine)

    assert save(session, mine, None, None, on_calendar=True) == "removed"

    assert calendar.deleted == [("primary", "evt_1")] and len(calendar.posted) == 1


def test_clearing_dates_that_were_never_on_a_calendar_asks_nobody(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    save(session, mine, on_calendar=False)

    assert save(session, mine, None, None, on_calendar=False) == "off"

    assert calendar.deleted == [] and mine.asked == []


@pytest.mark.parametrize(
    "trouble", [TransientIntegrationError("down"), PermanentIntegrationError("refused")]
)
def test_a_removal_google_does_not_answer_is_queued_for_another_try(
    session: Session, calendar: FakeCalendar, mine: Any, trouble: Exception
) -> None:
    save(session, mine)
    calendar.fail = trouble

    assert save(session, mine, None, None, on_calendar=False) == "removal_queued"

    assert pause(session) is None, "the dates are cleared all the same"
    (queued,) = session.scalars(select(ExtCalendarCleanup)).all()
    assert (queued.user_id, queued.event_id) == (READER, "evt_1")


# --- a calendar that is not there, or does not answer ---------------------------


def test_a_person_with_no_calendar_keeps_their_dates_and_is_told(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    assert save(session, mine, user_id="user_lee") == "not_connected"

    row = pause(session, "user_lee")
    assert row is not None and row.calendar_event_id is None
    assert calendar.posted == [], "and nobody else's calendar is written instead"


def test_a_grant_that_must_be_given_again_reads_as_not_connected(
    session: Session, calendar: FakeCalendar
) -> None:
    def refused(_user_id: str) -> Any:
        raise ReconnectRequiredError("connect again")

    assert save(session, refused) == "not_connected"
    row = pause(session)
    assert row is not None and row.calendar_event_id is None


@pytest.mark.parametrize(
    "trouble",
    [
        TransientIntegrationError("down"),
        PermanentIntegrationError("refused"),
        RuntimeError("anything at all"),
    ],
)
def test_a_calendar_that_cannot_be_reached_saves_the_dates_and_says_failed(
    session: Session, trouble: Exception
) -> None:
    def unreachable(_user_id: str) -> Any:
        raise trouble

    assert save(session, unreachable) == "failed"
    row = pause(session)
    assert row is not None and (row.starts_on, row.ends_on) == (FIRST, LAST)


def test_a_write_google_refuses_saves_the_dates_and_keeps_the_event_it_had(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    save(session, mine)
    calendar.fail = TransientIntegrationError("down")

    assert save(session, mine, LATER_FIRST, LATER_LAST) == "failed"

    row = pause(session)
    assert row is not None and (row.starts_on, row.ends_on) == (LATER_FIRST, LATER_LAST)
    assert row.calendar_event_id == "evt_1", "the next save can still move it"


def test_dates_that_make_no_sense_change_nothing_anywhere(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    save(session, mine)
    session.commit()

    with pytest.raises(AutuneError):
        save(session, mine, LAST, FIRST)
    session.rollback()  # what the request does with a refused body

    row = pause(session)
    assert row is not None and (row.starts_on, row.ends_on) == (FIRST, LAST)
    assert calendar.patched == [] and len(calendar.posted) == 1


# --- what is kept, and what is said ------------------------------------------


def test_after_the_last_day_the_id_goes_with_the_dates_and_the_event_stays(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    save(session, mine)

    assert service.forget_ended_pauses(session, today=LAST + timedelta(days=1)) == 1

    assert pause(session) is None
    assert calendar.deleted == [], "their own record of a leave they took"
    assert session.scalars(select(ExtCalendarCleanup)).all() == []


def test_no_log_line_says_when_the_person_is_away(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    with structlog.testing.capture_logs() as logs:
        save(session, mine)
        calendar.fail = TransientIntegrationError("down")
        save(session, mine, LATER_FIRST, LATER_LAST)
        save(session, mine, None, None, on_calendar=False)

    said = repr(logs)
    for day in (FIRST, LAST, LATER_FIRST, LATER_LAST):
        assert day.isoformat() not in said
    assert logs, "the failures are logged -- by kind"


def test_an_event_on_a_calendar_no_longer_connected_is_said_to_be_left_there(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    """Not ``not_connected``, which is about an event that was not put there:
    this one is there, and Autune can no longer take it off (mminjae97's
    review of #922)."""

    def refused(_user_id: str) -> Any:
        raise ReconnectRequiredError("connect again")

    assert save(session, mine) == "added"

    assert save(session, refused, on_calendar=False) == "not_removed"

    row = pause(session)
    assert row is not None and row.calendar_event_id is None
    assert calendar.deleted == []
    assert session.scalars(select(ExtCalendarCleanup)).all() == [], "nothing to try again with"


def test_clearing_the_dates_with_the_calendar_gone_says_the_event_is_left_too(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    assert save(session, mine) == "added"

    assert save(session, lambda _user: None, None, None, on_calendar=False) == "not_removed"

    assert pause(session) is None
    assert calendar.deleted == []


# --- Google is asked with nothing open, one save at a time -------------------------


class Watched(FakeCalendar):
    """A calendar that looks at the session while it is being asked, and lets
    something else happen in that moment."""

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self.open: list[bool] = []
        self.meanwhile: Any = None

    def _asked(self) -> None:
        self.open.append(self.session.in_transaction())
        if self.meanwhile is not None:
            happens, self.meanwhile = self.meanwhile, None
            happens()

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        self._asked()
        return super().request(method, path, json=json)

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        self._asked()
        super().delete_event(calendar_id, event_id)


@pytest.fixture
def watched(session: Session) -> Watched:
    return Watched(session)


@pytest.fixture
def theirs(watched: Watched) -> Any:
    return lambda user_id: (watched, "primary") if user_id == READER else None


def claim(session: Session, user_id: str = READER) -> datetime | None:
    row = pause(session, user_id)
    assert row is not None
    return row.calendar_claimed_at


def test_google_is_asked_with_no_transaction_open(
    session: Session, watched: Watched, theirs: Any
) -> None:
    """Writing, moving and removing: the dates are committed before each call,
    so a slow calendar holds neither the row nor a connection."""
    assert save(session, theirs) == "added"
    assert save(session, theirs, LATER_FIRST, LATER_LAST) == "added"
    assert save(session, theirs, LATER_FIRST, LATER_LAST, on_calendar=False) == "removed"
    assert save(session, theirs) == "added"
    assert save(session, theirs, None, None, on_calendar=False) == "removed"

    assert watched.open == [False] * 5
    assert watched.patched == ["evt_1"]
    assert watched.deleted == [("primary", "evt_1"), ("primary", "evt_2")]


def test_the_dates_stand_before_google_has_answered(
    session: Session, watched: Watched, theirs: Any
) -> None:
    seen: list[Any] = []

    def look() -> None:
        row = session.get(ExtNotificationPause, READER)
        assert row is not None
        seen.append((row.starts_on, row.ends_on, row.calendar_event_id))
        assert row.calendar_claimed_at is not None
        session.rollback()

    watched.meanwhile = look

    assert save(session, theirs) == "added"

    assert seen == [(FIRST, LAST, None)]
    assert claim(session) is None, "and the claim comes off with the answer"


@pytest.mark.parametrize(
    "second",
    [
        {"first": LATER_FIRST, "last": LATER_LAST, "on_calendar": True},
        {"first": LATER_FIRST, "last": LATER_LAST, "on_calendar": False},
        {"first": None, "last": None, "on_calendar": False},
    ],
    ids=["another range", "unticked", "cleared"],
)
def test_a_save_while_an_earlier_one_is_at_the_calendar_is_refused_whole(
    session: Session, watched: Watched, theirs: Any, second: dict[str, Any]
) -> None:
    """The double press: one event, and the dates the first save set -- the
    second changes nothing, not the dates either."""

    def press_again() -> None:
        with pytest.raises(ConflictError):
            save(
                session, theirs, second["first"], second["last"], on_calendar=second["on_calendar"]
            )

    watched.meanwhile = press_again

    assert save(session, theirs) == "added"

    assert len(watched.posted) == 1 and watched.patched == [] and watched.deleted == []
    row = pause(session)
    assert row is not None
    assert (row.starts_on, row.ends_on, row.calendar_event_id) == (FIRST, LAST, "evt_1")
    assert row.calendar_claimed_at is None

    # Once the first is back, the same save goes through.
    save(session, theirs, second["first"], second["last"], on_calendar=second["on_calendar"])
    assert len(watched.posted) == 1, "and still moves or removes that one event"


def test_a_claim_nobody_came_back_for_stops_holding_after_a_while(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    """A process that died at the calendar leaves its claim behind."""
    save(session, mine, on_calendar=False)
    row = pause(session)
    assert row is not None
    row.calendar_claimed_at = NOW - leave_calendar.CLAIM_FOR + timedelta(seconds=1)
    session.commit()

    with pytest.raises(ConflictError):
        save(session, mine, LATER_FIRST, LATER_LAST)
    held = pause(session)
    assert held is not None and (held.starts_on, held.ends_on) == (FIRST, LAST)
    assert calendar.posted == []

    held.calendar_claimed_at = NOW - leave_calendar.CLAIM_FOR
    session.commit()

    assert save(session, mine, LATER_FIRST, LATER_LAST) == "added"
    row = pause(session)
    assert row is not None
    assert (row.starts_on, row.calendar_event_id, row.calendar_claimed_at) == (
        LATER_FIRST,
        "evt_1",
        None,
    )


@pytest.mark.parametrize("row_is", ["taken over", "gone", "gone with the account"])
def test_a_save_that_lost_its_claim_writes_nothing_and_its_new_event_is_taken_off(
    session: Session, watched: Watched, theirs: Any, row_is: str
) -> None:
    """It was out longer than ``CLAIM_FOR`` and a later save took the row, or
    the pause ended meanwhile: the row is no longer this save's to write, and
    the event it made must not stay with nothing pointing at it."""

    def lose_it() -> None:
        row = session.get(ExtNotificationPause, READER)
        assert row is not None
        if row_is == "gone with the account":
            session.delete(session.get(User, READER))
        if row_is != "taken over":
            session.delete(row)
        else:
            row.calendar_claimed_at = NOW + timedelta(minutes=5)
            row.calendar_event_id = "evt_theirs"
        session.commit()

    watched.meanwhile = lose_it

    assert save(session, theirs) == "failed"

    row = pause(session)
    queued = [(q.user_id, q.event_id) for q in session.scalars(select(ExtCalendarCleanup))]
    if row_is == "taken over":
        assert row is not None and row.calendar_event_id == "evt_theirs"
        assert row.calendar_claimed_at is not None, "the later save's claim is not this one's"
    else:
        assert row is None
    if row_is == "gone with the account":
        # Nobody to remove it for, and no grant left to remove it with.
        assert queued == []
    else:
        assert queued == [(READER, "evt_1")]


def test_trouble_nobody_expected_at_google_lets_go_of_the_claim(
    session: Session, calendar: FakeCalendar, mine: Any
) -> None:
    """Not an ``IntegrationError``: it is raised, as before -- and the next
    save is not kept out for two minutes by it."""
    calendar.fail = RuntimeError("anything at all")

    with pytest.raises(RuntimeError):
        save(session, mine)

    row = pause(session)
    assert row is not None
    assert (row.starts_on, row.calendar_event_id, row.calendar_claimed_at) == (FIRST, None, None)
    calendar.fail = None
    assert save(session, mine) == "added"


def test_the_grant_is_read_and_the_session_let_go_before_google_is_asked(
    session: Session, monkeypatch: pytest.MonkeyPatch, calendar: FakeCalendar
) -> None:
    """``tasks.set_leave``'s own client builder: reading the grant must not
    reopen a transaction for the token refresh and the calls to sit in."""
    open_at: dict[str, bool] = {}

    def load(_s: Session, user_id: str, _service: str) -> UserIntegrationConfig:
        session.execute(select(User.id)).all()  # the read the real one makes
        return UserIntegrationConfig("calendar", user_id, "refresh-me")

    def refresh(**_: Any) -> str:
        open_at["refresh"] = session.in_transaction()
        return "token"

    monkeypatch.setattr(tasks, "load_user_integration", load)
    monkeypatch.setattr(tasks, "refresh_access_token", refresh)
    monkeypatch.setattr(tasks, "CalendarClient", lambda _token: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(
        tasks,
        "get_core_settings",
        lambda: SimpleNamespace(google_integration_credentials=("client-1", "secret")),
    )

    with tasks._calendars(session, release_after_read=True) as calendar_for:
        assert calendar_for(READER) is not None
    assert open_at == {"refresh": False}

    with tasks._calendars(session) as calendar_for:
        assert calendar_for(READER) is not None
    assert open_at == {"refresh": True}, "every other caller keeps its transaction"
    session.rollback()


# --- the routes ----------------------------------------------------------------


@pytest.fixture
def connected(monkeypatch: pytest.MonkeyPatch, calendar: FakeCalendar) -> dict[str, Any]:
    """The reader's calendar grant as the routes see it; ``state["grant"]``
    is what ``user_integrations`` holds for them."""
    state: dict[str, Any] = {
        "grant": UserIntegrationConfig("calendar", READER, "refresh-me"),
        "client": ("client-1", "secret"),
    }

    def load(_s: Session, user_id: str, _service: str) -> UserIntegrationConfig | None:
        return state["grant"] if user_id == READER else None

    @contextmanager
    def calendars(_session: Session, **_: Any) -> Iterator[Any]:
        yield (
            lambda user_id: (
                (calendar, "primary") if user_id == READER and state["grant"] is not None else None
            )
        )

    monkeypatch.setattr(leave_calendar, "load_user_integration", load)
    monkeypatch.setattr(
        leave_calendar,
        "get_core_settings",
        lambda: SimpleNamespace(google_integration_credentials=state["client"]),
    )
    monkeypatch.setattr(tasks, "_calendars", calendars)
    return state


@pytest.fixture
def api(session: Session, connected: dict[str, Any]) -> TestClient:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    session.add(Team(id="team_1", name="team_1"))
    sign_in(app, session, team_id="team_1")
    return TestClient(app)


def put(api: TestClient, **body: Any) -> dict[str, Any]:
    answer = api.put(f"{PREFIX}/me/notification-pause", json=body)
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


def test_the_read_says_whether_a_calendar_is_connected_and_nothing_else_about_it(
    api: TestClient, connected: dict[str, Any]
) -> None:
    assert api.get(f"{PREFIX}/me/notification-pause").json() == {
        "starts_on": None,
        "ends_on": None,
        "on_calendar": False,
        "calendar_leave": False,
        "calendar_connected": True,
        "calendar": None,
    }

    connected["grant"] = None
    assert api.get(f"{PREFIX}/me/notification-pause").json()["calendar_connected"] is False

    connected["grant"] = UserIntegrationConfig(
        "calendar", READER, "refresh-me", {"grant_revoked": True}
    )
    assert api.get(f"{PREFIX}/me/notification-pause").json()["calendar_connected"] is False


@pytest.mark.parametrize(
    ("grant_config", "client", "offered"),
    [
        ({}, ("client-1", "secret"), True),
        ({"client_id": "client-1"}, ("client-1", "secret"), True),
        # What ``tasks._calendars`` answers ``None`` or "connect again" for:
        ({"client_id": "client-0"}, ("client-1", "secret"), False),
        ({}, ("", ""), False),
        ({}, ("client-1", ""), False),
    ],
)
def test_the_box_is_not_offered_where_a_tick_could_only_fail(
    api: TestClient,
    connected: dict[str, Any],
    grant_config: dict[str, Any],
    client: tuple[str, str],
    offered: bool,
) -> None:
    connected["grant"] = UserIntegrationConfig("calendar", READER, "refresh-me", grant_config)
    connected["client"] = client

    assert api.get(f"{PREFIX}/me/notification-pause").json()["calendar_connected"] is offered


def test_a_save_without_the_field_is_a_save_without_the_tick(
    api: TestClient, calendar: FakeCalendar
) -> None:
    """The screen before this change sends two fields; it must keep meaning
    what it meant."""
    answer = put(api, starts_on=FIRST.isoformat(), ends_on=LAST.isoformat())

    assert (answer["on_calendar"], answer["calendar"]) == (False, "off")
    assert calendar.posted == []


def test_a_ticked_save_answers_added_and_the_read_keeps_saying_so(
    api: TestClient, calendar: FakeCalendar
) -> None:
    answer = put(api, starts_on=FIRST.isoformat(), ends_on=LAST.isoformat(), on_calendar=True)

    assert (answer["on_calendar"], answer["calendar"]) == (True, "added")
    assert len(calendar.posted) == 1
    read = api.get(f"{PREFIX}/me/notification-pause").json()
    assert (read["on_calendar"], read["calendar"]) == (True, None)


def test_an_unticked_save_and_a_clear_take_the_event_off(
    api: TestClient, calendar: FakeCalendar
) -> None:
    put(api, starts_on=FIRST.isoformat(), ends_on=LAST.isoformat(), on_calendar=True)

    answer = put(api, starts_on=FIRST.isoformat(), ends_on=LAST.isoformat(), on_calendar=False)
    assert (answer["on_calendar"], answer["calendar"]) == (False, "removed")

    put(api, starts_on=FIRST.isoformat(), ends_on=LAST.isoformat(), on_calendar=True)
    cleared = put(api, starts_on=None, ends_on=None)
    assert (cleared["starts_on"], cleared["on_calendar"], cleared["calendar"]) == (
        None,
        False,
        "removed",
    )
    assert calendar.deleted == [("primary", "evt_1"), ("primary", "evt_2")]


def test_a_ticked_save_with_no_calendar_saves_the_dates_and_says_so(
    api: TestClient, connected: dict[str, Any], calendar: FakeCalendar
) -> None:
    connected["grant"] = None

    answer = put(api, starts_on=FIRST.isoformat(), ends_on=LAST.isoformat(), on_calendar=True)

    assert answer["starts_on"] == FIRST.isoformat()
    assert (answer["on_calendar"], answer["calendar"]) == (False, "not_connected")
    assert calendar.posted == []


def test_the_routes_name_nobody(api: TestClient) -> None:
    """Their own only: a field naming another person is refused, not ignored."""
    body = {
        "starts_on": FIRST.isoformat(),
        "ends_on": LAST.isoformat(),
        "on_calendar": True,
        "user_id": "user_lee",
    }
    assert api.put(f"{PREFIX}/me/notification-pause", json=body).status_code == 422


def test_an_unticked_save_with_the_calendar_gone_says_the_event_is_left(
    api: TestClient, connected: dict[str, Any], calendar: FakeCalendar
) -> None:
    put(api, starts_on=FIRST.isoformat(), ends_on=LAST.isoformat(), on_calendar=True)
    connected["grant"] = None

    answer = put(api, starts_on=FIRST.isoformat(), ends_on=LAST.isoformat(), on_calendar=False)

    assert (answer["on_calendar"], answer["calendar"]) == (False, "not_removed")
    assert calendar.deleted == []


def test_a_save_while_an_earlier_one_is_out_answers_409_and_changes_nothing(
    api: TestClient, session: Session, calendar: FakeCalendar
) -> None:
    put(api, starts_on=FIRST.isoformat(), ends_on=LAST.isoformat(), on_calendar=True)
    row = session.get(ExtNotificationPause, READER)
    assert row is not None
    row.calendar_claimed_at = datetime.now(tz=UTC)
    session.commit()

    answer = api.put(
        f"{PREFIX}/me/notification-pause",
        json={
            "starts_on": LATER_FIRST.isoformat(),
            "ends_on": LATER_LAST.isoformat(),
            "on_calendar": True,
        },
    )

    assert answer.status_code == 409, answer.text
    read = api.get(f"{PREFIX}/me/notification-pause").json()
    assert (read["starts_on"], read["ends_on"]) == (FIRST.isoformat(), LAST.isoformat())
    assert calendar.patched == [] and len(calendar.posted) == 1
