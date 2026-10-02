"""Each person's own due dates on their own calendar, both ways (#435):
``calendar_sync``, the tasks that wire it to people's grants, and the dev
connect route.

SQLite in memory, one ``FakeCalendar`` per person.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Meeting, PrivacyViolationError, TeamMember, User, Utterance
from autune_core.settings import Settings as CoreSettings
from autune_core.user_integrations import UserIntegrationConfig
from autune_extraction import calendar_sync, service, tasks
from autune_extraction.calendar_sync import (
    ITEM_KEY,
    TAG,
    pull_calendar_changes,
    sync_due_date_to_calendar,
)
from autune_extraction.dev import routes
from autune_extraction.dev.page import PAGE
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtCalendarCleanup,
    ExtCalendarEvent,
    ExtCalendarPoll,
    ExtEditEvent,
    ExtExternalRef,
)
from autune_integrations import (
    CalendarEvent,
    PermanentIntegrationError,
    ReconnectRequiredError,
    TransientIntegrationError,
)
from autune_integrations.fakes import FakeCalendar

from .conftest import REMOVE_CALENDAR_EVENT, SYNC_ACTION_ITEM_CALENDAR

MEETING = "mtg_1"
SAID = "제가 금요일까지 릴리스 노트 정리하겠습니다"
ME, YOU, GONE = "user_me", "user_you", "user_gone"

TABLES = [
    Meeting.__table__,
    User.__table__,
    TeamMember.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtEditEvent.__table__,
    ExtCalendarEvent.__table__,
    ExtCalendarPoll.__table__,
    # An event Google would not delete is queued here for another try (#672).
    ExtCalendarCleanup.__table__,
    # ``has_copy_outside`` reads the refs before the events.
    ExtExternalRef.__table__,
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="스프린트 회의"))
        for uid, name, member in (
            (ME, "박지영", True),
            (YOU, "김개발", True),
            (GONE, "이건우", False),
        ):
            s.add(User(id=uid, email=f"{uid}@example.com", display_name=name))
            if member:
                s.add(TeamMember(team_id="team_1", user_id=uid))
        s.add(
            Utterance(
                id="utt_1",
                meeting_id=MEETING,
                speaker_label="SPEAKER_00",
                start_sec=0.0,
                end_sec=2.0,
                text=SAID,
            )
        )
        s.flush()
        yield s


def item(session: Session, *, status: str = "todo", **fields: Any) -> ExtActionItem:
    row = ExtActionItem(
        meeting_id=MEETING,
        description=fields.pop("description", "릴리스 노트 정리"),
        assignee_label=fields.pop("assignee_label", None),
        assignee_id=fields.pop("assignee_id", ME),
        due_date=fields.pop("due_date", date(2026, 10, 2)),
        due_text=fields.pop("due_text", "다음 주 금요일"),
        status=status,
        confidence=0.91,
        origin="model",
    )
    row.sources = [ExtActionItemSource(utterance_id="utt_1")]
    session.add(row)
    session.flush()
    return row


class Calendars:
    """One fake calendar per connected person."""

    def __init__(self, *connected: str) -> None:
        self.by_user = {uid: FakeCalendar() for uid in connected}

    def __call__(self, user_id: str) -> tuple[FakeCalendar, str] | None:
        cal = self.by_user.get(user_id)
        return (cal, "primary") if cal is not None else None

    def events(self, user_id: str) -> dict[str, dict]:
        return self.by_user[user_id].events


def sync(session: Session, calendars: Calendars, row: ExtActionItem) -> ExtCalendarEvent | None:
    return sync_due_date_to_calendar(session, calendars, action_item_id=row.id)


# --- out: the assignee's own calendar ---------------------------------------------


def test_a_confirmed_item_goes_on_its_assignees_own_calendar(session: Session) -> None:
    calendars = Calendars(ME, YOU)
    row = item(session)

    ref = sync(session, calendars, row)
    sync(session, calendars, row)

    assert ref is not None
    assert ref.user_id == ME
    assert list(calendars.events(ME)) == [ref.event_id]
    assert calendars.events(YOU) == {}  # team work is not copied to anyone else
    event = calendars.events(ME)[ref.event_id or ""]
    assert event["summary"] == "[마감] 릴리스 노트 정리"
    assert event["day"] == date(2026, 10, 2)
    assert event["private"] == {TAG[0]: TAG[1], ITEM_KEY: row.id}
    assert SAID not in str(event)
    assert "스프린트 회의" not in str(event)


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "needs_confirmation"},
        {"due_date": None},
        {"assignee_id": None, "assignee_label": "김개발"},  # a name as spoken, no account
        {"assignee_id": GONE},  # not on the meeting's team (ADR 0007)
    ],
)
def test_nothing_goes_anywhere_without_a_confirmed_dated_team_assignee(
    session: Session, fields: dict[str, Any]
) -> None:
    calendars = Calendars(ME, GONE)
    row = item(session, **fields)

    assert sync(session, calendars, row) is None
    assert all(cal.events == {} for cal in calendars.by_user.values())
    assert session.scalars(select(ExtCalendarEvent)).all() == []


def test_an_assignee_without_a_calendar_is_skipped(session: Session) -> None:
    assert sync(session, Calendars(YOU), item(session)) is None
    assert session.scalars(select(ExtCalendarEvent)).all() == []


def test_a_new_due_date_moves_the_same_event(session: Session) -> None:
    calendars = Calendars(ME)
    row = item(session)
    ref = sync(session, calendars, row)
    assert ref is not None

    row.due_date = date(2026, 10, 6)
    sync(session, calendars, row)

    assert list(calendars.events(ME)) == [ref.event_id]
    assert calendars.events(ME)[ref.event_id or ""]["day"] == date(2026, 10, 6)
    assert ref.synced_due_date == date(2026, 10, 6)


def test_reassigning_moves_the_event_to_the_new_assignees_calendar(session: Session) -> None:
    calendars = Calendars(ME, YOU)
    row = item(session)
    first = sync(session, calendars, row)
    assert first is not None
    old_event = first.event_id

    row.assignee_id = YOU
    moved = sync(session, calendars, row)

    assert calendars.by_user[ME].deleted == [old_event]
    assert moved is not None
    assert moved.user_id == YOU
    assert list(calendars.events(YOU)) == [moved.event_id]


def test_a_removed_due_date_removes_the_event(session: Session) -> None:
    calendars = Calendars(ME)
    row = item(session)
    ref = sync(session, calendars, row)
    assert ref is not None
    event_id = ref.event_id

    row.due_date = None
    assert sync(session, calendars, row) is None

    assert calendars.by_user[ME].deleted == [event_id]
    assert session.scalars(select(ExtCalendarEvent)).all() == []


def test_a_finished_item_keeps_its_event_marked_done(session: Session) -> None:
    calendars = Calendars(ME)
    row = item(session)
    ref = sync(session, calendars, row)
    assert ref is not None

    row.status = "done"
    sync(session, calendars, row)

    assert calendars.events(ME)[ref.event_id or ""]["summary"].startswith("[완료] ")


def test_an_event_deleted_by_hand_is_made_again_on_the_next_edit(session: Session) -> None:
    calendars = Calendars(ME)
    row = item(session)
    ref = sync(session, calendars, row)
    assert ref is not None
    first = ref.event_id
    calendars.events(ME).clear()

    row.due_date = date(2026, 10, 7)
    sync(session, calendars, row)

    assert ref.event_id != first
    assert calendars.events(ME)[ref.event_id or ""]["day"] == date(2026, 10, 7)


# --- back: a date the person moved on their own calendar ----------------------------


SINCE = datetime(2026, 9, 29, tzinfo=UTC)


def _moved(
    event_id: str, item_id: str, day: date | None, *, cancelled: bool = False
) -> CalendarEvent:
    return CalendarEvent(
        id=event_id,
        summary="",
        start=day,
        end=None,
        private={TAG[0]: TAG[1], ITEM_KEY: item_id},
        cancelled=cancelled,
    )


def _synced(session: Session) -> tuple[ExtActionItem, ExtCalendarEvent, FakeCalendar]:
    calendars = Calendars(ME)
    row = item(session)
    ref = sync(session, calendars, row)
    assert ref is not None
    assert ref.event_id
    return row, ref, calendars.by_user[ME]


def _pull(session: Session, cal: FakeCalendar, user_id: str = ME) -> list[str]:
    return pull_calendar_changes(session, cal, user_id=user_id, calendar_id="primary", since=SINCE)


def test_a_date_moved_in_calendar_becomes_the_due_date(session: Session) -> None:
    row, ref, cal = _synced(session)
    cal.changed = [_moved(ref.event_id or "", row.id, date(2026, 10, 9))]

    assert _pull(session, cal) == [row.id]
    assert row.due_date == date(2026, 10, 9)
    assert row.due_text is None  # the spoken phrase no longer explains the date
    assert ref.synced_due_date == date(2026, 10, 9)
    # Recorded like an edit on the board.
    assert session.scalars(select(ExtEditEvent.kind)).all() == ["edited"]


def test_a_date_autune_wrote_itself_is_not_an_edit(session: Session) -> None:
    row, ref, cal = _synced(session)
    cal.changed = [_moved(ref.event_id or "", row.id, date(2026, 10, 2))]

    assert _pull(session, cal) == []
    assert session.scalars(select(ExtEditEvent)).all() == []


def test_a_timed_event_is_read_as_its_day(session: Session) -> None:
    row, ref, cal = _synced(session)
    cal.changed = [
        CalendarEvent(
            id=ref.event_id or "",
            summary="",
            start=datetime(2026, 10, 8, 14, tzinfo=UTC),
            end=None,
            private={TAG[0]: TAG[1], ITEM_KEY: row.id},
        )
    ]

    _pull(session, cal)

    assert row.due_date == date(2026, 10, 8)


def test_an_event_deleted_in_calendar_is_let_go(session: Session) -> None:
    row, ref, cal = _synced(session)
    cal.changed = [_moved(ref.event_id or "", row.id, None, cancelled=True)]

    assert _pull(session, cal) == []
    assert row.due_date == date(2026, 10, 2)  # the task keeps its date
    assert session.scalars(select(ExtCalendarEvent)).all() == []


def test_only_the_assignees_own_event_counts(session: Session) -> None:
    """Read as someone else, or after the item was reassigned: ignored."""
    row, ref, cal = _synced(session)
    cal.changed = [_moved(ref.event_id or "", row.id, date(2026, 10, 9))]

    assert _pull(session, cal, user_id=YOU) == []

    row.assignee_id = YOU
    assert _pull(session, cal) == []
    assert row.due_date == date(2026, 10, 2)


# --- the tasks ------------------------------------------------------------------------


class ClosableCalendar(FakeCalendar):
    closed: bool = False

    def close(self) -> None:
        self.closed = True


def core_settings(
    *, sign_in: tuple[str, str] = ("", ""), integration: tuple[str, str] = ("", "")
) -> CoreSettings:
    """Core's real settings with only the Google clients given -- so which
    client a grant is refreshed with is decided by core, not by a stand-in
    that agrees with whatever the code under test reads. No ``.env``, and
    all four named, so a developer's own values cannot leak in."""
    return CoreSettings(
        _env_file=None,
        env="local",
        google_client_id=sign_in[0],
        google_client_secret=sign_in[1],
        google_integration_client_id=integration[0],
        google_integration_client_secret=integration[1],
        # An integration client is refused without it (core, review of #700).
        google_redirect_uri="http://localhost:3000/api/auth/google/callback",
    )


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    monkeypatch.setattr(tasks, "sync_action_item_calendar", SYNC_ACTION_ITEM_CALENDAR)
    monkeypatch.setattr(tasks, "remove_calendar_event", REMOVE_CALENDAR_EVENT)
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(
        tasks, "get_core_settings", lambda: core_settings(sign_in=("cid", "csecret"))
    )
    return session


def _grants(monkeypatch: pytest.MonkeyPatch, **tokens: str) -> None:
    def load(_s: Session, user_id: str, service: str) -> UserIntegrationConfig | None:
        token = tokens.get(user_id)
        if token is None:
            return None
        return UserIntegrationConfig(service=service, user_id=user_id, secret=token)

    monkeypatch.setattr(tasks, "load_user_integration", load)
    monkeypatch.setattr(tasks, "users_with_integration", lambda _s, _svc: sorted(tokens))


def test_the_task_uses_the_assignees_own_grant(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calendar = ClosableCalendar()
    refreshed: list[str] = []

    def refresh(**kw: str) -> str:
        refreshed.append(kw["refresh_token"])
        return "access"

    _grants(monkeypatch, user_me="refresh-me", user_you="refresh-you")
    monkeypatch.setattr(tasks, "refresh_access_token", refresh)
    monkeypatch.setattr(tasks, "CalendarClient", lambda token: calendar)

    tasks.sync_action_item_calendar(item(wired).id)

    assert refreshed == ["refresh-me"]  # only the assignee's own token
    assert len(calendar.events) == 1
    assert calendar.closed


def test_a_deployment_without_google_client_credentials_touches_nobody(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blank Google client settings: nobody is reached."""

    def never(**_: str) -> str:
        raise AssertionError("no refresh without client credentials")

    _grants(monkeypatch, user_me="refresh-me")
    monkeypatch.setattr(tasks, "get_core_settings", core_settings)
    monkeypatch.setattr(tasks, "refresh_access_token", never)

    tasks.sync_action_item_calendar(item(wired).id)

    assert wired.scalars(select(ExtCalendarEvent)).all() == []


def _refreshed_with(
    wired: Session, monkeypatch: pytest.MonkeyPatch, settings: CoreSettings
) -> list[tuple[str, str]]:
    used: list[tuple[str, str]] = []

    def refresh(**kw: str) -> str:
        used.append((kw["client_id"], kw["client_secret"]))
        return "access"

    _grants(monkeypatch, user_me="refresh-me")
    monkeypatch.setattr(tasks, "get_core_settings", lambda: settings)
    monkeypatch.setattr(tasks, "refresh_access_token", refresh)
    monkeypatch.setattr(tasks, "CalendarClient", lambda token: ClosableCalendar())
    tasks.sync_action_item_calendar(item(wired).id)
    return used


def test_a_grant_is_refreshed_with_the_integration_client_when_there_is_one(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refresh token is bound to the client it was issued to. The calendar
    is connected through the integration client when the deployment has one
    (core's ``integration_google``), so that is the one to refresh with --
    the sign-in client's credentials would be refused."""
    both = core_settings(sign_in=("login-id", "login-secret"), integration=("cal-id", "cal-s"))

    assert _refreshed_with(wired, monkeypatch, both) == [("cal-id", "cal-s")]


def test_without_an_integration_client_the_sign_in_client_refreshes(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What every deployment did before the second client existed."""
    only_sign_in = core_settings(sign_in=("login-id", "login-secret"))

    assert _refreshed_with(wired, monkeypatch, only_sign_in) == [("login-id", "login-secret")]


def _grant_issued_to(monkeypatch: pytest.MonkeyPatch, issued_to: str | None) -> list[str]:
    """One connected person whose grant records ``issued_to``; returns the
    refresh calls made, so a test can say Google was or was not asked."""
    asked: list[str] = []

    def load(_s: Session, user_id: str, service: str) -> UserIntegrationConfig | None:
        config = {"calendar_id": "primary"} | ({"client_id": issued_to} if issued_to else {})
        return UserIntegrationConfig(service, user_id, "refresh-me", config)

    def refresh(**kw: str) -> str:
        asked.append(kw["client_id"])
        return "access"

    monkeypatch.setattr(tasks, "load_user_integration", load)
    monkeypatch.setattr(tasks, "refresh_access_token", refresh)
    monkeypatch.setattr(tasks, "CalendarClient", lambda token: ClosableCalendar())
    monkeypatch.setattr(
        tasks, "get_core_settings", lambda: core_settings(sign_in=("current-client", "s"))
    )
    return asked


def test_a_grant_issued_to_another_client_is_not_sent_to_google(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mkkim68, review of #700: the refresh would be refused -- a token works
    only with the client that issued it -- so it is not made, and the caller
    gets the error a refused refresh would have given."""
    asked = _grant_issued_to(monkeypatch, "the-old-client")

    with pytest.raises(ReconnectRequiredError), tasks._calendars(session) as calendar_for:
        calendar_for(ME)

    assert asked == []


@pytest.mark.parametrize("issued_to", ["current-client", None])
def test_a_grant_issued_to_this_client_or_to_nobody_on_record_is_refreshed(
    session: Session, monkeypatch: pytest.MonkeyPatch, issued_to: str | None
) -> None:
    """``None``: connected before the client was recorded. Unknown is not
    broken, so it is tried as it always was."""
    asked = _grant_issued_to(monkeypatch, issued_to)

    with tasks._calendars(session) as calendar_for:
        assert calendar_for(ME) is not None

    assert asked == ["current-client"]


def test_an_integration_client_alone_is_enough_to_reach_calendars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sign-in switched off does not switch calendars off with it."""
    monkeypatch.setattr(
        tasks, "get_core_settings", lambda: core_settings(integration=("cal-id", "cal-s"))
    )
    assert tasks._google_client_configured()

    monkeypatch.setattr(tasks, "get_core_settings", core_settings)
    assert not tasks._google_client_configured()


def test_the_pull_reads_each_person_on_their_own_and_keeps_a_cursor(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = item(wired)
    calendar = ClosableCalendar()
    wired.add(
        ExtCalendarEvent(
            action_item_id=row.id,
            meeting_id=MEETING,
            user_id=ME,
            event_id="evt_me",
            synced_due_date=date(2026, 10, 2),
        )
    )
    wired.flush()
    calendar.changed = [_moved("evt_me", row.id, date(2026, 10, 9))]

    def refresh(**kw: str) -> str:
        if kw["refresh_token"] == "revoked":
            raise ReconnectRequiredError("google refused the refresh token with 400")
        return "access"

    notion: list[str] = []
    _grants(monkeypatch, user_a_revoked="revoked", user_me="refresh-me")
    monkeypatch.setattr(tasks, "refresh_access_token", refresh)
    monkeypatch.setattr(tasks, "CalendarClient", lambda token: calendar)
    monkeypatch.setattr(tasks, "sync_action_item", notion.append)

    tasks.pull_calendar_changes()

    assert row.due_date == date(2026, 10, 9)  # the revoked person did not stop me
    assert notion == [row.id]  # Notion follows the new date
    assert wired.get(ExtCalendarPoll, ME) is not None
    assert wired.get(ExtCalendarPoll, "user_a_revoked") is None


def test_the_pull_is_a_periodic_task() -> None:
    assert tasks.pull_calendar_changes.name == "autune.extraction.periodic.pull_calendar_changes"


def test_notion_and_calendar_fail_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def notion_down(_: str) -> None:
        calls.append("notion")
        raise PermanentIntegrationError("notion rejected the page")

    def calendar_down(_: str) -> None:
        calls.append("calendar")
        raise ReconnectRequiredError("google refused the refresh token with 400")

    monkeypatch.setattr(tasks, "sync_action_item", notion_down)
    monkeypatch.setattr(tasks, "sync_action_item_calendar", calendar_down)

    tasks.sync_after_confirmation("act_1")  # must not raise

    assert calls == ["notion", "calendar"]


def test_the_calendar_task_does_not_retry_itself() -> None:
    """A timed-out create may have made the event; a retry would make a second."""
    assert not getattr(SYNC_ACTION_ITEM_CALENDAR, "autoretry_for", ())


# --- the dev connect route -------------------------------------------------------------


class _Session:
    def commit(self) -> None:
        pass


def _connect_calendar(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sign_in: tuple[str, str] = ("", ""),
    integration: tuple[str, str] = ("", ""),
) -> dict[str, Any]:
    saved: dict[str, Any] = {}
    monkeypatch.setattr(
        routes,
        "get_core_settings",
        lambda: core_settings(sign_in=sign_in, integration=integration),
    )
    monkeypatch.setattr(
        routes,
        "save_user_integration",
        lambda _s, user_id, service, **kw: saved.update(kw, user_id=user_id, service=service),
    )
    monkeypatch.setattr(
        routes, "forget_calendar_cursor", lambda _s, user_id: saved.update(forgot=user_id)
    )
    return saved


def test_connecting_a_calendar_checks_it_and_stores_it_as_that_persons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = _connect_calendar(monkeypatch, sign_in=("cid", "cs"))
    calendar = ClosableCalendar()
    monkeypatch.setattr(routes, "refresh_access_token", lambda **_: "access")
    monkeypatch.setattr(routes, "CalendarClient", lambda token: calendar)

    body = {"user_id": ME, "refresh_token": "1//r"}
    result = routes.connect_calendar(body, _Session())  # type: ignore[arg-type]

    assert result["status"] == "connected"
    assert saved == {
        "user_id": ME,
        "service": "calendar",
        "secret": "1//r",
        "config": {"calendar_id": "primary"},
        "forgot": ME,  # a reconnect restarts the read-back
    }
    assert calendar.closed


def test_the_dev_connect_checks_a_token_with_the_integration_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pasted refresh token is checked with the client the timer will later
    refresh it with, or a token that passed here would fail there."""
    _connect_calendar(monkeypatch, sign_in=("login-id", "login-s"), integration=("cal-id", "cal-s"))
    used: list[tuple[str, str]] = []

    def refresh(**kw: str) -> str:
        used.append((kw["client_id"], kw["client_secret"]))
        return "access"

    monkeypatch.setattr(routes, "refresh_access_token", refresh)
    monkeypatch.setattr(routes, "CalendarClient", lambda token: ClosableCalendar())

    routes.connect_calendar({"user_id": ME, "refresh_token": "1//r"}, _Session())  # type: ignore[arg-type]

    assert used == [("cal-id", "cal-s")]


def test_a_refused_refresh_token_is_refused_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = _connect_calendar(monkeypatch, sign_in=("cid", "cs"))

    def refused(**_: str) -> str:
        raise ReconnectRequiredError("google refused the refresh token with 400")

    monkeypatch.setattr(routes, "refresh_access_token", refused)

    body = {"user_id": ME, "refresh_token": "1//secret-refresh"}
    with pytest.raises(routes.HTTPException) as caught:
        routes.connect_calendar(body, _Session())  # type: ignore[arg-type]

    assert caught.value.status_code == 401
    assert "secret-refresh" not in str(caught.value.detail)
    assert saved == {}


def test_without_google_client_credentials_nothing_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = _connect_calendar(monkeypatch)

    body = {"user_id": ME, "refresh_token": "1//r"}
    with pytest.raises(routes.HTTPException) as caught:
        routes.connect_calendar(body, _Session())  # type: ignore[arg-type]

    assert caught.value.status_code == 400
    assert saved == {}


def test_the_calendar_page_sends_exactly_the_fields_the_route_reads() -> None:
    script = PAGE.split("function connectCalendar()", 1)[1].split("}, ", 1)[0]
    sent = set(re.findall(r"^\s+(\w+): document\.getElementById", script, re.M))
    assert sent == set(routes.ConnectCalendar.model_fields)


def test_the_calendar_service_name_is_the_grants() -> None:
    assert calendar_sync.CALENDAR == "calendar"


# --- review of #441 ------------------------------------------------------------------


class _DeleteDown(FakeCalendar):
    """Google is reachable for everything but a delete."""

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        raise TransientIntegrationError("google timed out")


def test_an_event_google_would_not_delete_is_queued_for_another_try(session: Session) -> None:
    """mkkim68, review of #670: the row went whether or not the delete got
    through, and after that nothing knew the event's id -- its title, the
    item's description, stayed on the old assignee's calendar for good (#672)."""
    calendars = Calendars(ME, YOU)
    row = item(session)
    first = sync(session, calendars, row)
    assert first is not None
    event_id = first.event_id
    calendars.by_user[ME] = _DeleteDown(events=dict(calendars.events(ME)))
    row.assignee_id = YOU

    moved = sync(session, calendars, row)

    assert moved is not None and moved.user_id == YOU, "the item still moves on"
    queued = session.scalars(select(ExtCalendarCleanup)).all()
    assert [(q.user_id, q.event_id) for q in queued] == [(ME, event_id)]


def test_a_deleted_event_queues_nothing(session: Session) -> None:
    calendars = Calendars(ME, YOU)
    row = item(session)
    sync(session, calendars, row)
    row.assignee_id = YOU

    sync(session, calendars, row)

    assert session.scalars(select(ExtCalendarCleanup)).all() == []


def test_moving_an_item_back_takes_its_event_off_and_it_counted_as_a_copy_until_then(
    session: Session,
) -> None:
    """#672: an event is a copy outside like a page or an issue. Counted, a move
    back to 확인 필요 queues the sync that removes it; not counted, the event
    stayed, and a deleted speech later took the draft and left the title."""
    calendars = Calendars(ME)
    row = item(session)
    sync(session, calendars, row)
    row.status = "needs_confirmation"
    session.flush()

    assert service.has_copy_outside(session, row.id) is True
    assert service.copies_follow(session, row) is True

    assert sync(session, calendars, row) is None

    assert calendars.events(ME) == {}
    assert service.has_copy_outside(session, row.id) is False


def test_a_previous_assignees_lost_grant_does_not_block_the_new_one(session: Session) -> None:
    """The probe from review: the old grant is refused, the item moves anyway."""
    calendars = Calendars(ME, YOU)
    row = item(session)
    sync(session, calendars, row)
    row.assignee_id = YOU

    def revoked_me(user_id: str) -> tuple[FakeCalendar, str] | None:
        if user_id == ME:
            raise ReconnectRequiredError("google refused the refresh token with 400")
        return calendars(user_id)

    moved = sync_due_date_to_calendar(session, revoked_me, action_item_id=row.id)

    assert moved is not None
    assert moved.user_id == YOU
    assert list(calendars.events(YOU)) == [moved.event_id]


def test_one_persons_unexpected_failure_does_not_stop_the_others(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def pull_one(user_id: str) -> list[str]:
        seen.append(user_id)
        if user_id == "user_a":
            raise KeyError("a field the answer did not have")
        return []

    monkeypatch.setattr(tasks, "users_with_integration", lambda _s, _svc: ["user_a", "user_b"])
    monkeypatch.setattr(tasks, "_pull_one", pull_one)

    tasks.pull_calendar_changes()

    assert seen == ["user_a", "user_b"]


def test_a_privacy_block_on_the_notion_step_is_logged_as_a_block(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocked, not failed: its own event, and the next item still syncs."""
    events: list[str] = []
    synced: list[str] = []

    def notion(action_item_id: str) -> None:
        if action_item_id == "act_blocked":
            raise PrivacyViolationError("unmasked phone number")
        synced.append(action_item_id)

    monkeypatch.setattr(tasks, "users_with_integration", lambda _s, _svc: ["user_a"])
    monkeypatch.setattr(tasks, "_pull_one", lambda _u: ["act_blocked", "act_ok"])
    monkeypatch.setattr(tasks, "sync_action_item", notion)
    monkeypatch.setattr(tasks.log, "warning", lambda event, **_: events.append(event))

    tasks.pull_calendar_changes()

    assert events == ["extraction_notion_sync_blocked_by_privacy_guard"]
    assert synced == ["act_ok"]


def _connected_me(monkeypatch: pytest.MonkeyPatch, calendar: FakeCalendar) -> None:
    _grants(monkeypatch, user_me="refresh-me")
    monkeypatch.setattr(tasks, "refresh_access_token", lambda **_: "access")
    monkeypatch.setattr(tasks, "CalendarClient", lambda token: calendar)


def _polled(session: Session) -> datetime:
    row = session.get(ExtCalendarPoll, ME, populate_existing=True)
    assert row is not None
    return row.polled_at.replace(tzinfo=UTC)


def test_the_cursor_is_one_row_moved_forward(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connected_me(monkeypatch, ClosableCalendar())

    tasks._pull_one(ME)
    first = _polled(wired)
    tasks._pull_one(ME)

    assert len(wired.scalars(select(ExtCalendarPoll)).all()) == 1
    assert _polled(wired) >= first


def test_a_cursor_google_no_longer_keeps_falls_back_to_the_lookback(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """410 updatedMinTooLongAgo: a person back after a lapsed grant."""
    asked: list[datetime] = []

    class Stale(ClosableCalendar):
        def changed_events(self, calendar_id: str, **kw: Any) -> list[CalendarEvent]:
            asked.append(kw["updated_min"])
            if len(asked) == 1:
                raise PermanentIntegrationError("gone", upstream_status=410)
            return []

    wired.add(ExtCalendarPoll(user_id=ME, polled_at=datetime(2026, 1, 1, tzinfo=UTC)))
    wired.flush()
    _connected_me(monkeypatch, Stale())

    tasks._pull_one(ME)

    assert len(asked) == 2
    assert asked[1] > datetime(2026, 9, 1, tzinfo=UTC)
    assert _polled(wired) > datetime(2026, 9, 1, tzinfo=UTC)


def test_deleting_an_item_takes_its_event_off_the_calendar(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calendar = ClosableCalendar()
    _connected_me(monkeypatch, calendar)
    row = item(wired)
    tasks.sync_action_item_calendar(row.id)
    (event_id,) = list(calendar.events)

    tasks.remove_calendar_event(row.id)

    assert calendar.deleted == [event_id]


def test_an_event_that_could_not_be_removed_with_its_item_is_queued(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The item's row cascades away with the item; the queue is what still
    knows the event (#672)."""

    class DeleteDown(ClosableCalendar):
        def delete_event(self, calendar_id: str, event_id: str) -> None:
            raise TransientIntegrationError("google timed out")

    calendar = DeleteDown()
    _connected_me(monkeypatch, calendar)
    row = item(wired)
    tasks.sync_action_item_calendar(row.id)
    (event_id,) = list(calendar.events)

    tasks.remove_calendar_event(row.id)  # must not raise

    queued = wired.scalars(select(ExtCalendarCleanup)).all()
    assert [(q.user_id, q.event_id) for q in queued] == [(ME, event_id)]


def test_an_unreachable_calendar_never_blocks_a_deletion(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = item(wired)
    wired.add(
        ExtCalendarEvent(
            action_item_id=row.id,
            meeting_id=MEETING,
            user_id=ME,
            event_id="evt_me",
            synced_due_date=date(2026, 10, 2),
        )
    )
    wired.flush()

    def refused(**_: str) -> str:
        raise ReconnectRequiredError("google refused the refresh token with 400")

    _grants(monkeypatch, user_me="revoked")
    monkeypatch.setattr(tasks, "refresh_access_token", refused)

    tasks.remove_calendar_event(row.id)  # must not raise
