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
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import Base, Meeting, PrivacyViolationError, Team, TeamMember, User
from autune_extraction import days_off, reminders, service, tasks
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


def test_an_event_at_the_end_of_the_calendar_breaks_nothing() -> None:
    """9999-12-31 with no end: there is no day after it to count up to. It is
    skipped, and the events around it are still read (review of #838)."""
    text = (
        "BEGIN:VEVENT\nDTSTART;VALUE=DATE:99991231\nEND:VEVENT\n"
        "BEGIN:VEVENT\nDTSTART;VALUE=DATE:99991225\nDTEND;VALUE=DATE:99991231\nEND:VEVENT\n"
        "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20261009\nDTEND;VALUE=DATE:20261010\nEND:VEVENT\n"
    )

    days = days_off.parse_holiday_ics(text)

    assert date(2026, 10, 9) in days
    assert date(9999, 12, 31) in days and date(9999, 12, 25) in days


@pytest.mark.parametrize(
    ("line", "day"),
    [
        pytest.param("DTSTART:20261008T150000Z", date(2026, 10, 9), id="utc-midnight-in-seoul"),
        pytest.param("DTSTART:20261009T010000Z", date(2026, 10, 9), id="utc-morning"),
        pytest.param("DTSTART:20261009T000000", date(2026, 10, 9), id="no-zone"),
        pytest.param("DTSTART;VALUE=DATE:20261009", date(2026, 10, 9), id="a-date"),
    ],
)
def test_a_day_is_the_day_it_is_in_korea(line: str, day: date) -> None:
    text = f"BEGIN:VEVENT\n{line}\nEND:VEVENT\n"

    assert days_off.parse_holiday_ics(text) == {day}


def test_a_utc_event_ends_on_its_korean_day_too() -> None:
    text = "BEGIN:VEVENT\nDTSTART:20261008T150000Z\nDTEND:20261009T150000Z\nEND:VEVENT\n"

    assert days_off.parse_holiday_ics(text) == {date(2026, 10, 9)}


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


def test_an_answer_longer_than_any_calendar_is_given_up_on() -> None:
    """Nothing says how long somebody else's file is until it is read."""
    padding = "X-PAD:" + "x" * 1000 + "\r\n"
    huge = ICS + padding * (days_off.MAX_ICS_BYTES // len(padding) + 1)

    with pytest.raises(PermanentIntegrationError):
        days_off.fetch_public_holidays(today=TUESDAY, http=answering(200, huge))
    assert HANGUL_DAY in days_off.fetch_public_holidays(
        today=TUESDAY, http=answering(200, ICS + padding * 10)
    )


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
    linked: set[str] = field(default_factory=lambda: {"user_kim", "user_lee"})
    """Who linked a Slack account for DMs."""
    open_scopes: int = 0
    """How many ``session_scope`` blocks the task has open right now."""
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
        made.open_scopes += 1
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            made.open_scopes -= 1

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

    def linked(_: Session, user_id: str, service: str) -> SimpleNamespace | None:
        assert service == "slack"
        return SimpleNamespace(config={"slack_user_id": "U1"}) if user_id in made.linked else None

    monkeypatch.setattr(tasks, "load_integration", lambda s, team, service: _Config("xoxb"))
    monkeypatch.setattr(tasks, "load_user_integration", linked)
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


def test_a_privacy_refusal_while_asking_is_raised_as_that_and_claims_nothing(
    session: Session, world: World
) -> None:
    """Not read as "unknown, so send", and not taken for a refused MESSAGE
    either: that would settle the day's claim for a DM nobody refused, and the
    person would get none that day (review of #841)."""
    world.calendars["user_kim"] = PrivacyViolationError("refused")

    with pytest.raises(PrivacyViolationError, match="read of a person's calendar.*user_kim"):
        tasks.send_daily_digests()

    assert [m.channel for m in world.slack.sent] == ["user_lee"], "the others still go"
    assert [d.user_id for d in session.query(ExtDailyDigest)] == ["user_lee"]

    world.calendars["user_kim"] = FakeCalendar()
    assert tasks.send_daily_digests() == ["user_kim"], "and theirs goes once it can be asked"


# --- asked last, outside the send, and nothing kept (reviews of #838 and #841) -------------


def owed_kim(session: Session) -> service.DailyDigestOwed:
    (found,) = [
        d
        for d in service.daily_digests_to_send(session, now=TUESDAY_10_KST)
        if d.user_id == "user_kim"
    ]
    return found


def test_would_go_is_every_reason_not_to_send_that_autune_can_see(session: Session) -> None:
    """What the task asks before it reads anybody's calendar: the reminder
    switch, the person's own pause, and whether there is anything to say."""
    owed = owed_kim(session)
    assert service.daily_digest_would_go(session, owed, now=TUESDAY_10_KST) is True

    service.set_due_reminders(session, "user_kim", on=False, now=TUESDAY_10_KST)
    assert service.daily_digest_would_go(session, owed, now=TUESDAY_10_KST) is False
    service.set_due_reminders(session, "user_kim", on=True, now=TUESDAY_10_KST)

    service.set_notification_pause(
        session, "user_kim", starts_on=TUESDAY, ends_on=TUESDAY, now=TUESDAY_10_KST
    )
    assert service.daily_digest_would_go(session, owed, now=TUESDAY_10_KST) is False
    service.set_notification_pause(
        session, "user_kim", starts_on=None, ends_on=None, now=TUESDAY_10_KST
    )

    session.query(ExtActionItem).filter_by(assignee_id="user_kim").delete()
    session.flush()
    assert service.daily_digest_would_go(session, owed, now=TUESDAY_10_KST) is False
    assert session.query(ExtDailyDigest).count() == 0, "asking claims nothing"


def test_mondays_would_go_is_the_same_question(session: Session) -> None:
    (owed, _other) = service.weekly_digests_to_send(session, now=PLAIN_MONDAY_10_KST)
    assert service.weekly_digest_would_go(session, owed, now=PLAIN_MONDAY_10_KST) is True

    service.set_due_reminders(session, owed.user_id, on=False, now=PLAIN_MONDAY_10_KST)
    assert service.weekly_digest_would_go(session, owed, now=PLAIN_MONDAY_10_KST) is False
    service.set_due_reminders(session, owed.user_id, on=True, now=PLAIN_MONDAY_10_KST)

    monday = date(2026, 10, 12)
    service.set_notification_pause(
        session, owed.user_id, starts_on=monday, ends_on=monday, now=PLAIN_MONDAY_10_KST
    )
    assert service.weekly_digest_would_go(session, owed, now=PLAIN_MONDAY_10_KST) is False
    service.set_notification_pause(
        session, owed.user_id, starts_on=None, ends_on=None, now=PLAIN_MONDAY_10_KST
    )

    session.query(ExtActionItem).filter_by(assignee_id=owed.user_id).delete()
    session.flush()
    assert service.weekly_digest_would_go(session, owed, now=PLAIN_MONDAY_10_KST) is False
    assert session.query(ExtWeeklyDigest).count() == 0


@pytest.mark.parametrize("task", ["send_daily_digests", "send_weekly_digests"])
def test_no_calendar_is_read_for_a_message_that_would_not_go(
    session: Session, world: World, monkeypatch: pytest.MonkeyPatch, task: str
) -> None:
    """Every cheaper reason first: somebody whose digest would not go anyway
    does not have their calendar read to learn what is already known."""
    if task == "send_weekly_digests":
        _Clock.moment = PLAIN_MONDAY_10_KST
    asked = Asked(away=[(date(2026, 10, 1), date(2026, 10, 31))])
    world.calendars["user_kim"] = asked
    monkeypatch.setattr(service, "daily_digest_would_go", lambda *_, **__: False)
    monkeypatch.setattr(service, "weekly_digest_would_go", lambda *_, **__: False)

    getattr(tasks, task)()

    assert asked.asked == []


@pytest.mark.parametrize("task", ["send_daily_digests", "send_weekly_digests"])
def test_the_calendar_is_asked_with_no_transaction_open(
    session: Session, world: World, monkeypatch: pytest.MonkeyPatch, task: str
) -> None:
    """Google may take ten seconds to answer. The question is put after the
    transaction that asked ``would_go`` has closed and before the send's
    opens (review of #841)."""
    if task == "send_weekly_digests":
        _Clock.moment = PLAIN_MONDAY_10_KST
    open_when_asked: list[int] = []

    def out_of_office(user_id: str, now: datetime) -> bool:
        open_when_asked.append(world.open_scopes)
        return False

    monkeypatch.setattr(tasks, "_out_of_office", out_of_office)

    assert sorted(getattr(tasks, task)()) == ["user_kim", "user_lee"]

    assert open_when_asked == [0, 0]


def test_nobodys_calendar_is_read_for_a_dm_that_cannot_reach_them(
    session: Session, world: World
) -> None:
    """Somebody with no linked Slack account gets no DM whatever their calendar
    says -- so it is not asked, this run or the seventeen after it."""
    asked = Asked(away=[(TUESDAY, date(2026, 10, 8))])
    world.calendars["user_kim"] = asked
    world.linked.discard("user_kim")

    for minute in (0, 10, 20):
        _Clock.moment = kst(10, minute).astimezone(UTC)
        tasks.send_daily_digests()

    assert asked.asked == []


def run_logged(task: str) -> tuple[list[str], list[dict[str, object]]]:
    with capture_logs() as logs:
        went = getattr(tasks, task)()
    return went, logs


def without_items_of(session: Session, user_id: str) -> None:
    session.query(ExtActionItem).filter_by(assignee_id=user_id).delete()
    session.flush()


@pytest.mark.parametrize(
    ("task", "moment", "away"),
    [
        pytest.param(
            "send_daily_digests", TUESDAY_10_KST, (TUESDAY, date(2026, 10, 8)), id="morning"
        ),
        pytest.param(
            "send_weekly_digests",
            PLAIN_MONDAY_10_KST,
            (date(2026, 10, 12), date(2026, 10, 13)),
            id="monday",
        ),
    ],
)
def test_a_run_that_holds_somebody_back_is_a_run_with_nothing_to_send(
    session: Session,
    world: World,
    task: str,
    moment: datetime,
    away: tuple[date, date],
) -> None:
    """Review of #841. ``owed=1 sent=0`` with no failure beside it was that
    one person's absence, and the run it turned into ``sent=1`` was the time
    they came back. Where calendars are read, a held-back run and a run with
    nothing to send leave the same thing behind: the same return, the same
    log lines, the same rows."""
    _Clock.moment = moment
    table = ExtDailyDigest if task == "send_daily_digests" else ExtWeeklyDigest
    without_items_of(session, "user_lee")
    world.calendars["user_kim"] = FakeCalendar(away=[away])

    held_back = run_logged(task)
    rows_held_back = session.query(table).count()

    without_items_of(session, "user_kim")
    nothing_to_send = run_logged(task)

    assert held_back == nothing_to_send == ([], [])
    assert rows_held_back == session.query(table).count() == 0


def test_one_sent_and_one_held_back_is_one_sent(session: Session, world: World) -> None:
    """The same, beside somebody whose DM does go: nothing says another
    person was owed one."""
    world.calendars["user_kim"] = FakeCalendar(away=[(TUESDAY, date(2026, 10, 8))])

    beside_a_held_back = run_logged("send_daily_digests")

    session.query(ExtDailyDigest).delete()
    without_items_of(session, "user_kim")
    world.slack.sent.clear()
    alone = run_logged("send_daily_digests")

    assert beside_a_held_back == alone
    assert beside_a_held_back[0] == ["user_lee"]


def test_where_no_calendar_is_read_the_summary_is_logged_as_before(
    session: Session, world: World
) -> None:
    world.settings["leave_from_calendar"] = False

    _went, logs = run_logged("send_daily_digests")

    assert [e for e in logs if e["event"] == "extraction_daily_digests_sent"] == [
        {
            "event": "extraction_daily_digests_sent",
            "log_level": "info",
            "owed": 2,
            "sent": 2,
            "not_linked": 0,
        }
    ]


@pytest.mark.parametrize(
    ("task", "moment", "event", "would_go"),
    [
        pytest.param(
            "send_daily_digests",
            TUESDAY_10_KST,
            "extraction_daily_digest_failed",
            "daily_digest_would_go",
            id="morning",
        ),
        pytest.param(
            "send_weekly_digests",
            PLAIN_MONDAY_10_KST,
            "extraction_weekly_digest_failed",
            "weekly_digest_would_go",
            id="monday",
        ),
    ],
)
def test_a_failure_while_asking_about_one_person_is_that_persons_only(
    session: Session,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    task: str,
    moment: datetime,
    event: str,
    would_go: str,
) -> None:
    """Review of #841. The question was moved out of the send's ``try``, and
    with it out of "an unexpected error is that one digest's": a database
    error while asking ended the whole run, so the people after it got
    nothing. It fails as the send fails -- logged by type, and on to the next."""
    _Clock.moment = moment
    real = getattr(service, would_go)

    def flaky(session_: Session, *args: object, **kwargs: object) -> bool:
        owed = kwargs.get("owed") or kwargs.get("digest") or args[0]
        if owed.user_id == "user_kim":  # type: ignore[union-attr]
            raise RuntimeError("could not connect to server: password=hunter2")
        return bool(real(session_, *args, **kwargs))

    monkeypatch.setattr(service, would_go, flaky)

    went, logs = run_logged(task)

    assert went == ["user_lee"], "the person after the failure still gets theirs"
    (failure,) = [e for e in logs if e["event"] == event]
    assert (failure["user_id"], failure["reason"]) == ("user_kim", "RuntimeError")
    assert "hunter2" not in repr(logs), "the type, never what the error said"


def test_a_refusal_collected_before_a_failure_is_still_raised(
    session: Session, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run must reach its end whatever one person's question runs into:
    that is where a privacy refusal collected earlier is raised."""
    world.calendars["user_kim"] = PrivacyViolationError("refused")
    real = service.daily_digest_would_go

    def flaky(session_: Session, *, owed: service.DailyDigestOwed, now: datetime) -> bool:
        if owed.user_id == "user_lee":
            raise RuntimeError("database gone")
        return real(session_, owed, now=now)

    monkeypatch.setattr(service, "daily_digest_would_go", flaky)

    with capture_logs() as logs, pytest.raises(PrivacyViolationError, match="user_kim"):
        tasks.send_daily_digests()

    assert [e["user_id"] for e in logs if e["event"] == "extraction_daily_digest_failed"] == [
        "user_lee"
    ]
    assert session.query(ExtDailyDigest).count() == 0


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


@pytest.mark.parametrize("switch", ["work_report", "after_meeting_notice"])
def test_the_holidays_are_read_where_only_another_message_that_keeps_working_days_is_on(
    session: Session, world: World, monkeypatch: pytest.MonkeyPatch, switch: str
) -> None:
    # Neither digest on: the work report and the notice after a meeting ask
    # the same table whether today is a working day, so the read still runs.
    world.settings.update({"daily_digest": False, "weekly_digest": False, switch: True})
    monkeypatch.setattr(days_off, "fetch_public_holidays", lambda *, today: {HANGUL_DAY})

    assert tasks.refresh_public_holidays() == 1

    assert {row.day for row in session.query(ExtPublicHoliday)} == {HANGUL_DAY}


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
    fetched: list[date] = []

    def fetch(*, today: date) -> set[date]:
        # Recorded, not raised: the task takes any error as a read that did
        # not happen, so an assertion in here would pass for the wrong reason.
        fetched.append(today)
        return {HANGUL_DAY}

    monkeypatch.setattr(days_off, "fetch_public_holidays", fetch)

    assert tasks.refresh_public_holidays() == 0
    assert fetched == []


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


def test_no_shape_of_the_answer_fails_the_task(
    session: Session, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The answer is somebody else's file. An error nobody listed -- the
    review of #838 found an ``OverflowError`` -- is a read that did not
    happen, not a task that died."""
    days_off.store_public_holidays(session, {HANGUL_DAY}, now=TUESDAY_10_KST)

    def fetch(*, today: date) -> set[date]:
        raise OverflowError("date value out of range")

    monkeypatch.setattr(days_off, "fetch_public_holidays", fetch)

    with capture_logs() as logs:
        assert tasks.refresh_public_holidays() == 0

    assert [row.day for row in session.query(ExtPublicHoliday)] == [HANGUL_DAY]
    assert [e["reason"] for e in logs if e["event"] == "extraction_public_holidays_not_read"] == [
        "OverflowError"
    ]


# --- a holiday Monday: the week's digest goes on the next working day ---------------------
#     (the user, 2026-10-05: "공휴일이 아닌 업무일에 요약")

HOLIDAY_MONDAY = date(2026, 10, 5)
WEDNESDAY_10_KST = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)


def holidays(*days: date):  # type: ignore[no-untyped-def]
    return lambda day: day in days


def test_the_digest_day_is_the_weeks_first_working_day() -> None:
    monday = HOLIDAY_MONDAY
    assert reminders.digest_day(monday) == monday
    assert reminders.digest_day(monday, holidays(monday)) == TUESDAY
    assert reminders.digest_day(monday, holidays(monday, TUESDAY)) == date(2026, 10, 7)
    whole_week = [monday + timedelta(days=n) for n in range(5)]
    assert reminders.digest_day(monday, holidays(*whole_week)) is None, "never a weekend"


def test_a_week_is_named_by_its_monday_whichever_day_its_digest_goes() -> None:
    off = holidays(HOLIDAY_MONDAY)

    assert reminders.digest_week(HOLIDAY_MONDAY_10_KST, off) is None, "not on the holiday"
    assert reminders.digest_week(TUESDAY_10_KST, off) == HOLIDAY_MONDAY
    assert reminders.digest_week(WEDNESDAY_10_KST, off) is None, "once, not every day after"
    assert reminders.digest_week(datetime(2026, 10, 6, 12, 0, tzinfo=UTC), off) is None  # 21:00
    # A week with no holiday is as it always was.
    assert reminders.digest_week(PLAIN_MONDAY_10_KST, off) == date(2026, 10, 12)
    assert reminders.digest_week(PLAIN_MONDAY_10_KST + timedelta(days=1), off) is None


def test_after_a_holiday_monday_the_digest_is_owed_on_tuesday_once(session: Session) -> None:
    assert service.weekly_digests_to_send(session, now=HOLIDAY_MONDAY_10_KST) == []

    owed = service.weekly_digests_to_send(session, now=TUESDAY_10_KST)

    assert [(d.user_id, d.week_start) for d in owed] == [
        ("user_kim", HOLIDAY_MONDAY),
        ("user_lee", HOLIDAY_MONDAY),
    ]
    slack = FakeSlack()
    for digest in owed:
        assert service.send_weekly_digest(session, slack, digest, now=TUESDAY_10_KST) is True
    assert [m.channel for m in slack.sent] == ["user_kim", "user_lee"]
    assert service.weekly_digests_to_send(session, now=TUESDAY_10_KST) == []
    assert service.weekly_digests_to_send(session, now=WEDNESDAY_10_KST) == []
    assert len(service.weekly_digests_to_send(session, now=PLAIN_MONDAY_10_KST)) == 2, (
        "the next week is a new one"
    )


def test_the_day_the_weeks_digest_goes_has_no_morning_dm(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As a Monday has none: one message that morning, not two."""
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None, weekly_digest=True),  # type: ignore[call-arg]
    )

    assert service.daily_digests_to_send(session, now=TUESDAY_10_KST) == []
    assert len(service.daily_digests_to_send(session, now=WEDNESDAY_10_KST)) == 2


def test_where_no_weekly_digest_is_sent_that_morning_keeps_its_dm(session: Session) -> None:
    """A deployment with only the morning DM must not go silent on the day a
    digest it does not send would have gone."""
    assert ExtractionSettings(_env_file=None).weekly_digest is False  # type: ignore[call-arg]

    assert len(service.daily_digests_to_send(session, now=TUESDAY_10_KST)) == 2


def test_a_pause_is_about_the_day_the_digest_goes_not_the_weeks_monday(
    session: Session,
) -> None:
    """Somebody who paused the holiday Monday only is back on Tuesday, and
    somebody who paused Tuesday is not told on Tuesday."""
    service.set_notification_pause(
        session,
        "user_kim",
        starts_on=HOLIDAY_MONDAY,
        ends_on=HOLIDAY_MONDAY,
        now=HOLIDAY_MONDAY_10_KST,
    )
    service.set_notification_pause(
        session, "user_lee", starts_on=TUESDAY, ends_on=TUESDAY, now=HOLIDAY_MONDAY_10_KST
    )

    owed = service.weekly_digests_to_send(session, now=TUESDAY_10_KST)

    assert [d.user_id for d in owed] == ["user_kim"]
    lee = service.WeeklyDigest(user_id="user_lee", team_id="team_1", week_start=HOLIDAY_MONDAY)
    assert service.weekly_digest_would_go(session, lee, now=TUESDAY_10_KST) is False
    assert service.weekly_digest_would_go(session, owed[0], now=TUESDAY_10_KST) is True


def test_what_is_late_is_late_as_of_the_day_it_is_sent(session: Session) -> None:
    """An item due on the holiday Monday is overdue in Tuesday's digest."""
    item = session.get(ExtActionItem, "act_user_kim")
    assert item is not None
    item.due_date = HOLIDAY_MONDAY
    session.flush()
    (owed, _lee) = service.weekly_digests_to_send(session, now=TUESDAY_10_KST)
    slack, on_monday = FakeSlack(), FakeSlack()

    service.send_weekly_digest(session, slack, owed, now=TUESDAY_10_KST)
    expected = reminders.build_weekly_digest(
        [reminders.DigestLine(item.description, item.due_date, "주간 회의")],
        today=TUESDAY,
        board_url=slack.sent[0].text.splitlines()[-1],
    )
    as_of_monday = reminders.build_weekly_digest(
        [reminders.DigestLine(item.description, item.due_date, "주간 회의")],
        today=HOLIDAY_MONDAY,
        board_url=slack.sent[0].text.splitlines()[-1],
    )

    assert slack.sent[0].text == expected
    assert expected != as_of_monday, "the two days read differently, or this checks nothing"
    assert on_monday.sent == []
