"""No digest on a public holiday, and none to a person who is out of office
(the user, 2026-10-05).

The rules under test: public holidays are read from Google's public calendar
with no credentials and kept whole; a read that is not a calendar of holidays
is refused and the last good one stays; while there is no fresh read the table
in code answers; a holiday stops the morning DM and Monday's digest; a person
whose own calendar marks them out of office right now is held back and not
claimed, so they get it when they are back; and a calendar that cannot be
read is not a person who is away.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import Base, Meeting, PrivacyViolationError, Team, TeamMember, User
from autune_extraction import days_off, service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtDailyDigest,
    ExtPublicHoliday,
    ExtWeeklyDigest,
)
from autune_extraction.slots import KST
from autune_integrations.calendar import ReconnectRequiredError
from autune_integrations.errors import PermanentIntegrationError, TransientIntegrationError
from autune_integrations.fakes import FakeCalendar, FakeSlack

TUESDAY = date(2026, 10, 6)
TUESDAY_10_KST = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)
HOLIDAY_MONDAY_10_KST = datetime(2026, 10, 5, 1, 0, tzinfo=UTC)  # 개천절's substitute day
PLAIN_MONDAY_10_KST = datetime(2026, 10, 12, 1, 0, tzinfo=UTC)
HANGUL_DAY = date(2026, 10, 9)  # a Friday
HANGUL_DAY_10_KST = datetime(2026, 10, 9, 1, 0, tzinfo=UTC)

ICS = "\r\n".join(
    [
        "BEGIN:VCALENDAR",
        "X-WR-CALNAME:대한민국의 휴일",
        "BEGIN:VEVENT",
        "DTSTART;VALUE=DATE:20261009",
        "DTEND;VALUE=DATE:20261010",
        "DESCRIPTION:공휴일",
        "SUMMARY:한글날",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "DTSTART;VALUE=DATE:20270206",
        "DTEND;VALUE=DATE:20270209",
        "DESCRIPTION:공휴일",
        "SUMMARY:설날 연휴",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "DTSTART;VALUE=DATE:20261224",
        "DTEND;VALUE=DATE:20261225",
        # Folded, as a long line is (RFC 5545): read unjoined it says "기념".
        "DESCRIPTION:기념",
        " 일\\n기념일을 숨기려면 설정으로 이동하세요",
        "SUMMARY:크리스마스 이브",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "DTSTART;VALUE=DATE:20261225",
        "DESCRIPTION:공휴일",
        "SUMMARY:크리스마스",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "DTSTART;VALUE=DATE:not-a-date",
        "SUMMARY:깨진 항목",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
)


# --- reading the calendar ----------------------------------------------------------


def test_every_day_of_an_event_is_a_holiday_and_its_end_is_exclusive() -> None:
    days = days_off.parse_holiday_ics(ICS)

    assert days == {
        date(2026, 10, 9),
        date(2027, 2, 6),
        date(2027, 2, 7),
        date(2027, 2, 8),
        date(2026, 12, 25),  # no DTEND: its start day
    }
    assert date(2026, 10, 10) not in days
    assert date(2026, 12, 24) not in days, "an observance is marked and worked"


def test_an_absurd_span_does_not_turn_months_into_holidays() -> None:
    text = "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20261001\nDTEND;VALUE=DATE:20270401\nEND:VEVENT\n"

    assert len(days_off.parse_holiday_ics(text)) == 14


def answering(status: int, text: str, seen: list[httpx.Request] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text=text)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_calendar_is_fetched_with_no_credentials_and_nothing_sent() -> None:
    seen: list[httpx.Request] = []

    days = days_off.fetch_public_holidays(today=TUESDAY, http=answering(200, ICS, seen))

    (request,) = seen
    assert request.method == "GET"
    assert str(request.url) == days_off.KOREA_HOLIDAYS_ICS
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert request.content == b"" and not request.url.query
    assert HANGUL_DAY in days


@pytest.mark.parametrize(
    ("status", "text", "error"),
    [
        pytest.param(503, "", TransientIntegrationError, id="outage"),
        pytest.param(429, "", TransientIntegrationError, id="rate-limited"),
        pytest.param(404, "", PermanentIntegrationError, id="moved"),
        pytest.param(302, ICS, PermanentIntegrationError, id="redirected"),
        pytest.param(203, ICS, PermanentIntegrationError, id="somebody-elses-copy"),
        pytest.param(200, "<html>Sign in</html>", PermanentIntegrationError, id="not-a-calendar"),
        pytest.param(
            200,
            "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20190101\nEND:VEVENT\n",
            PermanentIntegrationError,
            id="nothing-in-the-coming-year",
        ),
    ],
)
def test_an_answer_that_is_not_a_calendar_of_holidays_is_refused(
    status: int, text: str, error: type[Exception]
) -> None:
    with pytest.raises(error) as caught:
        days_off.fetch_public_holidays(today=TUESDAY, http=answering(status, text))
    assert "html" not in str(caught.value).lower(), "what Google said is not repeated"


def test_a_timeout_is_an_outage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(TransientIntegrationError):
        days_off.fetch_public_holidays(
            today=TUESDAY, http=httpx.Client(transport=httpx.MockTransport(handler))
        )


# --- the table in code ---------------------------------------------------------------


def test_the_table_in_code_knows_lunar_and_substitute_days() -> None:
    assert days_off.in_table(date(2026, 10, 5)), "개천절 fell on a Saturday"
    assert days_off.in_table(date(2026, 2, 17)), "설날, by the lunar calendar"
    assert days_off.in_table(HANGUL_DAY)
    assert not days_off.in_table(TUESDAY)


# --- a database ------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
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
    shared = {m.__tablename__ for m in (Meeting, User, Team, TeamMember)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Team(id="team_1", name="팀"))
        s.add(Meeting(id="mtg_1", team_id="team_1", title="주간 회의"))
        for who in ("user_kim", "user_lee"):
            s.add(TeamMember(team_id="team_1", user_id=who))
            s.add(
                ExtActionItem(
                    id=f"act_{who}",
                    meeting_id="mtg_1",
                    description=f"{who}의 일",
                    assignee_id=who,
                    status="todo",
                    confidence=0.9,
                    origin="model",
                )
            )
        s.flush()
        yield s


def test_the_calendar_answers_while_its_read_is_fresh(session: Session) -> None:
    """It knows what the table cannot -- a holiday declared last month -- and
    it is the one believed when the two differ."""
    declared = date(2026, 10, 8)
    days_off.store_public_holidays(session, {declared}, now=TUESDAY_10_KST)

    assert days_off.is_public_holiday(session, declared, now=TUESDAY_10_KST)
    assert not days_off.in_table(declared)
    assert not days_off.is_public_holiday(session, HANGUL_DAY, now=TUESDAY_10_KST), (
        "the calendar that was read does not list it"
    )


def test_the_table_answers_when_nothing_was_read_or_the_read_is_old(session: Session) -> None:
    assert days_off.is_public_holiday(session, HANGUL_DAY, now=TUESDAY_10_KST)

    days_off.store_public_holidays(session, {date(2026, 10, 8)}, now=TUESDAY_10_KST)
    later = TUESDAY_10_KST + days_off.FRESH_FOR + timedelta(hours=1)

    assert days_off.is_public_holiday(session, HANGUL_DAY, now=later)
    assert not days_off.is_public_holiday(session, date(2026, 10, 8), now=later)


def test_a_read_replaces_the_last_one_whole(session: Session) -> None:
    days_off.store_public_holidays(session, {date(2026, 10, 8), HANGUL_DAY}, now=TUESDAY_10_KST)
    later = TUESDAY_10_KST + timedelta(hours=12)

    assert days_off.store_public_holidays(session, {HANGUL_DAY}, now=later) == 1

    (row,) = session.query(ExtPublicHoliday).all()
    assert row.day == HANGUL_DAY
    assert row.read_at.replace(tzinfo=UTC) == later


def test_no_morning_dm_is_owed_on_a_public_holiday(session: Session) -> None:
    assert service.daily_digests_to_send(session, now=HANGUL_DAY_10_KST) == []
    assert [d.user_id for d in service.daily_digests_to_send(session, now=TUESDAY_10_KST)] == [
        "user_kim",
        "user_lee",
    ]


def test_no_monday_digest_is_owed_on_a_public_holiday(session: Session) -> None:
    assert service.weekly_digests_to_send(session, now=HOLIDAY_MONDAY_10_KST) == []
    assert [
        d.user_id for d in service.weekly_digests_to_send(session, now=PLAIN_MONDAY_10_KST)
    ] == [
        "user_kim",
        "user_lee",
    ]


def test_a_holiday_the_calendar_declared_stops_the_morning_dm(session: Session) -> None:
    days_off.store_public_holidays(session, {TUESDAY}, now=TUESDAY_10_KST)

    assert service.daily_digests_to_send(session, now=TUESDAY_10_KST) == []


# --- out of office ---------------------------------------------------------------------


@dataclass
class Asked(FakeCalendar):
    asked: list[tuple[str, datetime, datetime]] = field(default_factory=list)

    def out_of_office(
        self, calendar_id: str, time_min: datetime, time_max: datetime
    ) -> list[tuple[datetime | date, datetime | date]]:
        self.asked.append((calendar_id, time_min, time_max))
        return super().out_of_office(calendar_id, time_min, time_max)


def kst(hour: int, minute: int = 0, day: int = 6) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=KST)


@pytest.mark.parametrize(
    ("window", "away"),
    [
        pytest.param((kst(9), kst(18)), True, id="the-working-day"),
        pytest.param((kst(9), kst(10)), False, id="back-since-ten"),
        pytest.param((kst(10), kst(10, 1)), True, id="starts-this-minute"),
        pytest.param((kst(13), kst(18)), False, id="the-afternoon"),
        pytest.param((TUESDAY, date(2026, 10, 8)), True, id="all-day-today"),
        pytest.param((date(2026, 10, 5), TUESDAY), False, id="all-day-ended-yesterday"),
        pytest.param((date(2026, 10, 7), date(2026, 10, 8)), False, id="all-day-tomorrow"),
        pytest.param((TUESDAY, kst(18)), False, id="one-of-each"),
    ],
)
def test_away_is_an_out_of_office_entry_that_covers_this_moment(
    window: tuple[datetime | date, datetime | date], away: bool
) -> None:
    assert days_off.away_now(Asked(away=[window]), "primary", now=TUESDAY_10_KST) is away


def test_it_asks_about_this_minute_of_the_persons_own_calendar_and_no_more() -> None:
    calendar = Asked()

    assert days_off.away_now(calendar, "primary", now=TUESDAY_10_KST) is False

    assert calendar.asked == [("primary", TUESDAY_10_KST, TUESDAY_10_KST + timedelta(minutes=1))]


# --- the tasks ---------------------------------------------------------------------------


@dataclass
class _Config:
    secret: str

    def require_secret(self) -> str:
        return self.secret


class _Clock(datetime):
    moment = TUESDAY_10_KST

    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def, override]
        return cls.moment


@dataclass
class World:
    """What the tasks see: a fake Slack, each person's calendar (absent = not
    connected), and the settings."""

    slack: FakeSlack = field(default_factory=FakeSlack)
    calendars: dict[str, object] = field(default_factory=dict)
    settings: dict[str, bool] = field(
        default_factory=lambda: {
            "daily_digest": True,
            "weekly_digest": True,
            "leave_from_calendar": True,
        }
    )


@pytest.fixture
def world(session: Session, monkeypatch: pytest.MonkeyPatch) -> World:
    made = World()

    @contextmanager
    def scope() -> Iterator[Session]:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise

    @contextmanager
    def calendars(_: Session) -> Iterator[object]:
        def calendar_for(user_id: str) -> tuple[object, str] | None:
            found = made.calendars.get(user_id)
            if isinstance(found, Exception):
                raise found
            return None if found is None else (found, "primary")

        yield calendar_for

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "_calendars", calendars)
    monkeypatch.setattr(tasks, "load_integration", lambda s, team, service: _Config("xoxb"))
    monkeypatch.setattr(tasks, "SlackClient", lambda secret: made.slack)
    monkeypatch.setattr(tasks, "datetime", _Clock)
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None, **made.settings),  # type: ignore[call-arg]
    )
    _Clock.moment = TUESDAY_10_KST
    return made


def test_someone_out_of_office_is_held_back_not_claimed_and_gets_it_when_back(
    session: Session, world: World
) -> None:
    world.calendars["user_kim"] = FakeCalendar(away=[(kst(9), kst(11))])
    world.calendars["user_lee"] = FakeCalendar()

    assert tasks.send_daily_digests() == ["user_lee"]
    assert [d.user_id for d in session.query(ExtDailyDigest)] == ["user_lee"]

    _Clock.moment = kst(11, 10).astimezone(UTC)
    assert tasks.send_daily_digests() == ["user_kim"]
    assert [m.channel for m in world.slack.sent] == ["user_lee", "user_kim"]


def test_someone_away_all_morning_gets_none_that_day(session: Session, world: World) -> None:
    world.calendars["user_kim"] = FakeCalendar(away=[(TUESDAY, date(2026, 10, 8))])

    for minute in (0, 10, 20):
        _Clock.moment = kst(10, minute).astimezone(UTC)
        tasks.send_daily_digests()

    assert [m.channel for m in world.slack.sent] == ["user_lee"]
    assert [d.user_id for d in session.query(ExtDailyDigest)] == ["user_lee"]


def test_mondays_digest_is_held_back_the_same_way(session: Session, world: World) -> None:
    _Clock.moment = PLAIN_MONDAY_10_KST
    world.calendars["user_kim"] = FakeCalendar(away=[(date(2026, 10, 12), date(2026, 10, 13))])

    assert tasks.send_weekly_digests() == ["user_lee"]
    assert [d.user_id for d in session.query(ExtWeeklyDigest)] == ["user_lee"]


def test_no_calendar_is_asked_unless_the_deployment_turned_it_on(
    session: Session, world: World
) -> None:
    world.settings["leave_from_calendar"] = False
    asked = Asked(away=[(TUESDAY, date(2026, 10, 8))])
    world.calendars["user_kim"] = asked

    assert sorted(tasks.send_daily_digests()) == ["user_kim", "user_lee"]
    assert asked.asked == []
    assert ExtractionSettings(_env_file=None).leave_from_calendar is False  # type: ignore[call-arg]


def test_a_calendar_that_cannot_be_read_is_not_a_person_who_is_away(
    session: Session, world: World
) -> None:
    """A lapsed grant or an outage must not silence a person's own digest."""
    world.calendars["user_kim"] = ReconnectRequiredError("the refresh token secret-abc was refused")

    with capture_logs() as logs:
        assert sorted(tasks.send_daily_digests()) == ["user_kim", "user_lee"]

    (entry,) = [e for e in logs if e["event"] == "extraction_leave_not_read"]
    assert entry["user_id"] == "user_kim" and entry["reason"] == "ReconnectRequiredError"
    assert "secret-abc" not in repr(logs)


def test_a_privacy_refusal_while_asking_is_raised_not_read_as_unknown(
    session: Session, world: World
) -> None:
    world.calendars["user_kim"] = PrivacyViolationError("refused")

    with pytest.raises(PrivacyViolationError):
        tasks.send_daily_digests()


# --- the holiday read --------------------------------------------------------------------


def test_the_holidays_are_read_and_kept_where_a_digest_is_on(
    session: Session, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[date] = []

    def fetch(*, today: date) -> set[date]:
        asked.append(today)
        return {HANGUL_DAY, date(2026, 12, 25)}

    monkeypatch.setattr(days_off, "fetch_public_holidays", fetch)

    assert tasks.refresh_public_holidays() == 2

    assert asked == [TUESDAY]
    assert {row.day for row in session.query(ExtPublicHoliday)} == {HANGUL_DAY, date(2026, 12, 25)}


@pytest.mark.parametrize(
    "settings",
    [
        pytest.param({"daily_digest": False, "weekly_digest": False}, id="no-digest-on"),
        pytest.param({"public_holiday_calendar": False}, id="calendar-off"),
    ],
)
def test_no_call_is_made_where_nothing_would_use_it(
    world: World, monkeypatch: pytest.MonkeyPatch, settings: dict[str, bool]
) -> None:
    world.settings.update(settings)

    def fetch(*, today: date) -> set[date]:
        raise AssertionError("the calendar was fetched")

    monkeypatch.setattr(days_off, "fetch_public_holidays", fetch)

    assert tasks.refresh_public_holidays() == 0


def test_a_failed_read_keeps_the_last_good_one(
    session: Session, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    days_off.store_public_holidays(session, {HANGUL_DAY}, now=TUESDAY_10_KST)

    def fetch(*, today: date) -> set[date]:
        raise TransientIntegrationError("google_holiday_calendar timed out")

    monkeypatch.setattr(days_off, "fetch_public_holidays", fetch)

    with capture_logs() as logs:
        assert tasks.refresh_public_holidays() == 0

    assert [row.day for row in session.query(ExtPublicHoliday)] == [HANGUL_DAY]
    assert [e["reason"] for e in logs if e["event"] == "extraction_public_holidays_not_read"] == [
        "TransientIntegrationError"
    ]
