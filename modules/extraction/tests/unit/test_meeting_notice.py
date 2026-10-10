"""The DM right after a meeting (the user, 2026-10-07): ``meeting_notice``.

The rules under test: a count and a link, and **nothing of an unconfirmed
item** -- not its text, not its date; one notice a person and meeting; only
somebody on the meeting's team now, for items the pipeline made since the
last working day began; nobody who turned their reminders off or paused the
day, read again at send time; from 09:00 to 17:00 in Korea on a working day,
and otherwise at 09:00 on the next working day; a refusal by the outbound
check is raised, once, after the others have gone.

SQLite in memory, the real task, a fake Slack that runs the outbound check.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Meeting, PrivacyViolationError, Team, TeamMember, User
from autune_extraction import meeting_notice, reminders, service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.meeting_notice import (
    MeetingNotice,
    NoticeOwed,
    build_meeting_notice,
    notices_to_send,
    send_meeting_notice,
)
from autune_extraction.models import (
    ExtActionItem,
    ExtDueReminderOptOut,
    ExtMeetingNotice,
    ExtNotificationPause,
)
from autune_extraction.reminders import DigestLine
from autune_integrations.errors import TransientIntegrationError
from autune_integrations.fakes import FakeSlack as CheckedSlack

# Wednesday 2026-10-07 in Korea.
AT_10 = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)  # 10:00
AT_1659 = datetime(2026, 10, 7, 7, 59, tzinfo=UTC)
AT_17 = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
AT_0859 = datetime(2026, 10, 7, 23, 59, tzinfo=UTC)  # Thursday 08:59
NEXT_09 = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)  # Thursday 09:00
MEETING = "mtg_team_1"
SECRET_TEXT = "아무도 확인하지 않은 초안 문구"
SECRET_DATE = date(2026, 10, 30)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )
    # No public holidays unless a test names one: the week these tests use has
    # a real one on its Friday (한글날, 2026-10-09).
    monkeypatch.setattr(meeting_notice.days_off, "is_public_holiday", lambda *_, **__: False)


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
        for team in ("team_1", "team_2"):
            s.add(Team(id=team, name=team))
            s.add(Meeting(id=f"mtg_{team}", team_id=team, title=f"{team} 주간 <회의>"))
        for user in ("user_kim", "user_lee", "user_gone"):
            s.add(User(id=user, email=f"{user}@example.com", display_name=user))
        s.add(TeamMember(team_id="team_1", user_id="user_kim"))
        s.add(TeamMember(team_id="team_1", user_id="user_lee"))
        s.add(TeamMember(team_id="team_2", user_id="user_kim"))
        s.flush()
        yield s


def item(
    session: Session,
    item_id: str,
    *,
    who: str = "user_kim",
    meeting: str = MEETING,
    status: str = "needs_confirmation",
    text: str = SECRET_TEXT,
    due: date | None = SECRET_DATE,
    origin: str = "model",
    made: datetime = AT_10 - timedelta(minutes=10),
) -> ExtActionItem:
    row = ExtActionItem(
        id=item_id,
        meeting_id=meeting,
        description=text,
        assignee_id=who,
        status=status,
        due_date=due,
        confidence=0.9,
        origin=origin,
        created_at=made,
    )
    session.add(row)
    session.flush()
    return row


class FakeSlack:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_dm(self, user_id: str, text: str) -> str:
        self.sent.append((user_id, text))
        return "17000.1"


def owed(session: Session, now: datetime = AT_10) -> list[tuple[str, str]]:
    return [(n.meeting_id, n.user_id) for n in notices_to_send(session, now=now)]


KIM = NoticeOwed(meeting_id=MEETING, team_id="team_1", user_id="user_kim")


# --- what it says ----------------------------------------------------------------


def test_the_message_is_the_meeting_a_count_and_a_link() -> None:
    text = build_meeting_notice(
        MeetingNotice(meeting_title="주간 <회의>", waiting=3),
        actions_url="https://autune.example/meetings/mtg_1/actions",
        today=date(2026, 10, 7),
    )

    assert text.split("\n") == [
        "'주간 &lt;회의&gt;'에서 내 담당으로 잡힌 일 3건이 확인을 기다립니다.",
        "https://autune.example/meetings/mtg_1/actions",
    ]


def test_an_item_already_confirmed_is_named_with_its_date() -> None:
    text = build_meeting_notice(
        MeetingNotice(
            meeting_title="주간 회의",
            waiting=1,
            confirmed=[DigestLine("배포 <점검>", date(2026, 10, 9), None)],
        ),
        actions_url="u",
        today=date(2026, 10, 7),
    )

    assert text.split("\n") == [
        "'주간 회의'에서 내 담당으로 잡힌 일 1건이 확인을 기다립니다.",
        "• 확정: 배포 &lt;점검&gt; (기한 10월 9일 금)",
        "u",
    ]
    only = build_meeting_notice(
        MeetingNotice(
            meeting_title="주간 회의", waiting=0, confirmed=[DigestLine("일", None, None)]
        ),
        actions_url="u",
        today=date(2026, 10, 7),
    )
    assert only.split("\n") == [
        "'주간 회의'에서 내 담당으로 정해진 일이 있습니다.",
        "• 확정: 일",
        "u",
    ]


@pytest.mark.parametrize(
    ("day", "written"),
    [
        (date(2026, 10, 12), "10월 12일 월"),
        (date(2026, 10, 13), "10월 13일 화"),
        (date(2026, 10, 14), "10월 14일 수"),
        (date(2026, 10, 15), "10월 15일 목"),
        (date(2026, 10, 16), "10월 16일 금"),
        (date(2026, 10, 17), "10월 17일 토"),
        (date(2026, 10, 18), "10월 18일 일"),
        (date(2026, 1, 1), "1월 1일 목"),
        # Another year than the one it is read in is said.
        (date(2027, 1, 4), "2027년 1월 4일 월"),
        (date(2025, 12, 31), "2025년 12월 31일 수"),
    ],
)
def test_a_day_is_written_as_the_minutes_write_one(day: date, written: str) -> None:
    """The user, 2026-10-09: a due date here read ``2026-10-13`` while the 요약
    tab's minutes read "10월 13일 화"."""
    assert reminders.written_day(day, year=2026) == written

    text = build_meeting_notice(
        MeetingNotice(
            meeting_title="주간 회의", waiting=0, confirmed=[DigestLine("일", day, None)]
        ),
        actions_url="u",
        today=date(2026, 10, 7),
    )

    assert f"• 확정: 일 (기한 {written})" in text
    assert day.isoformat() not in text


def test_the_year_left_out_is_koreas_at_the_moment_it_is_sent(session: Session) -> None:
    """01:00 on 1 January in Korea is still 31 December in UTC."""
    at = datetime(2026, 12, 31, 16, 0, tzinfo=UTC)
    made = at - timedelta(minutes=10)
    item(session, "act_new", status="todo", text="새해 첫 일", due=date(2027, 1, 4), made=made)
    item(session, "act_old", status="todo", text="묵은 일", due=date(2026, 12, 30), made=made)
    slack = FakeSlack()

    assert send_meeting_notice(session, slack, KIM, now=at) is True  # type: ignore[arg-type]

    ((_, text),) = slack.sent
    assert "• 확정: 새해 첫 일 (기한 1월 4일 월)" in text
    assert "• 확정: 묵은 일 (기한 2026년 12월 30일 수)" in text


def test_nothing_of_an_unconfirmed_item_can_reach_the_message(session: Session) -> None:
    """The rule the notice is built around (#246; agent-layer.md, rule 3):
    through the real send, a draft's text and its date are not in what goes
    to Slack -- only that one waits. A confirmed item beside it is named."""
    item(session, "act_draft_1")
    item(session, "act_draft_2", text="또 다른 초안", due=date(2026, 11, 11))
    item(session, "act_done_deal", status="todo", text="확정된 일", due=date(2026, 10, 9))
    slack = FakeSlack()

    assert send_meeting_notice(session, slack, KIM, now=AT_10) is True  # type: ignore[arg-type]

    ((who, text),) = slack.sent
    assert who == "user_kim"
    assert "잡힌 일 2건이 확인을 기다립니다" in text
    # A draft's date in neither spelling: the notice names confirmed items only.
    for forbidden in (
        SECRET_TEXT,
        "또 다른 초안",
        "2026-10-30",
        "2026-11-11",
        "10월 30일",
        "11월 11일",
    ):
        assert forbidden not in text
    assert "• 확정: 확정된 일 (기한 10월 9일 금)" in text
    assert text.endswith("/meetings/mtg_team_1/actions")

    content = meeting_notice.notice_content(session, KIM, now=AT_10)
    assert content is not None
    assert SECRET_TEXT not in repr(content) and "2026, 10, 30" not in repr(content)


# --- who is owed -------------------------------------------------------------------


def test_one_is_owed_per_person_and_meeting_with_work_just_made(session: Session) -> None:
    item(session, "act_1")
    item(session, "act_2")
    item(session, "act_lee", who="user_lee")
    item(session, "act_other", meeting="mtg_team_2")

    assert owed(session) == [
        ("mtg_team_1", "user_kim"),
        ("mtg_team_1", "user_lee"),
        ("mtg_team_2", "user_kim"),
    ]


def test_who_is_not_owed_one(session: Session) -> None:
    item(session, "act_by_hand", origin="user", status="todo")  # a person added it themselves
    # Made on Monday morning: before the last working day (Tuesday) began.
    item(session, "act_old", who="user_lee", made=datetime(2026, 10, 5, 1, 0, tzinfo=UTC))
    item(session, "act_gone", who="user_gone")  # not on the meeting's team
    item(session, "act_done", who="user_lee", status="done")
    item(session, "act_nobody", who=None)  # type: ignore[arg-type]

    assert owed(session) == []


def test_a_speaker_identified_later_is_told_then(session: Session) -> None:
    """The item was made an hour ago with nobody on it; the sweep finds it the
    moment it has a holder."""
    row = item(session, "act_1", who=None, made=AT_10 - timedelta(hours=1))  # type: ignore[arg-type]
    assert owed(session) == []

    row.assignee_id = "user_kim"
    session.flush()

    assert owed(session) == [("mtg_team_1", "user_kim")]


def test_somebody_who_left_the_team_is_not_told(session: Session) -> None:
    item(session, "act_1", who="user_lee")
    session.query(TeamMember).filter_by(team_id="team_1", user_id="user_lee").delete()
    session.flush()

    assert owed(session) == []
    lee = NoticeOwed(meeting_id=MEETING, team_id="team_1", user_id="user_lee")
    assert send_meeting_notice(session, FakeSlack(), lee, now=AT_10) is False  # type: ignore[arg-type]


def test_a_notice_goes_once_and_later_items_of_the_meeting_send_no_second(
    session: Session,
) -> None:
    item(session, "act_1")
    slack = FakeSlack()

    assert send_meeting_notice(session, slack, KIM, now=AT_10) is True  # type: ignore[arg-type]
    assert send_meeting_notice(session, slack, KIM, now=AT_10) is False  # type: ignore[arg-type]
    item(session, "act_2", made=AT_10)

    assert owed(session) == []
    assert len(slack.sent) == 1
    assert [(n.meeting_id, n.user_id) for n in session.query(ExtMeetingNotice)] == [
        ("mtg_team_1", "user_kim")
    ]


def test_reminders_off_or_a_paused_day_means_no_notice_also_at_send_time(
    session: Session,
) -> None:
    item(session, "act_1")
    item(session, "act_lee", who="user_lee")
    session.add(ExtDueReminderOptOut(user_id="user_kim", created_at=AT_10))
    session.add(
        ExtNotificationPause(
            user_id="user_lee",
            starts_on=date(2026, 10, 7),
            ends_on=date(2026, 10, 7),
            created_at=AT_10,
        )
    )
    session.flush()

    assert owed(session) == []
    slack = FakeSlack()
    assert send_meeting_notice(session, slack, KIM, now=AT_10) is False  # type: ignore[arg-type]
    assert slack.sent == [] and session.query(ExtMeetingNotice).count() == 0


# --- when ------------------------------------------------------------------------


def test_it_goes_from_nine_to_five_in_korea_and_then_the_next_morning(session: Session) -> None:
    """The user, 2026-10-07: "시간은 09-17시 까지 전송. 넘으면 다음날 09시 전송"."""
    item(session, "act_1", made=AT_1659 - timedelta(minutes=5))

    assert owed(session, AT_1659) == [("mtg_team_1", "user_kim")]
    assert owed(session, AT_17) == []
    assert owed(session, AT_0859) == []
    assert owed(session, NEXT_09) == [("mtg_team_1", "user_kim")]


def test_an_item_made_after_five_is_still_inside_the_window_at_nine(session: Session) -> None:
    item(session, "act_1", made=AT_17 + timedelta(minutes=1))

    assert owed(session, AT_17 + timedelta(minutes=5)) == []
    assert owed(session, NEXT_09) == [("mtg_team_1", "user_kim")]
    assert owed(session, NEXT_09 + timedelta(hours=9)) == [], "past 17:00 again"
    # Not told on Thursday -- no linked account, say: by Friday it is old news.
    assert owed(session, NEXT_09 + timedelta(days=1)) == []


def test_a_friday_evening_is_told_on_monday_morning_not_on_the_weekend(
    session: Session,
) -> None:
    """The user, of "다음날": "다음 근무일"."""
    friday_1730 = datetime(2026, 10, 16, 8, 30, tzinfo=UTC)
    item(session, "act_1", made=friday_1730)

    assert owed(session, friday_1730 + timedelta(minutes=5)) == []
    assert owed(session, datetime(2026, 10, 17, 1, 0, tzinfo=UTC)) == [], "Saturday 10:00"
    assert owed(session, datetime(2026, 10, 18, 1, 0, tzinfo=UTC)) == [], "Sunday 10:00"
    assert owed(session, datetime(2026, 10, 18, 23, 59, tzinfo=UTC)) == [], "Monday 08:59"
    assert owed(session, datetime(2026, 10, 19, 0, 0, tzinfo=UTC)) == [
        ("mtg_team_1", "user_kim")
    ], "Monday 09:00"


def test_the_evening_before_a_holiday_is_told_on_the_next_working_day(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thursday 17:30, Friday a public holiday, then the weekend: Monday 09:00.
    The holiday is asked of the calendar the morning DM keeps."""
    monkeypatch.setattr(
        meeting_notice.days_off,
        "is_public_holiday",
        lambda _session, day, **_: day == date(2026, 10, 9),
    )
    thursday_1730 = datetime(2026, 10, 8, 8, 30, tzinfo=UTC)
    item(session, "act_1", made=thursday_1730)

    assert owed(session, datetime(2026, 10, 9, 1, 0, tzinfo=UTC)) == [], "the holiday, 10:00"
    assert owed(session, datetime(2026, 10, 10, 1, 0, tzinfo=UTC)) == [], "Saturday"
    assert owed(session, datetime(2026, 10, 12, 0, 0, tzinfo=UTC)) == [
        ("mtg_team_1", "user_kim")
    ], "Monday 09:00"
    # And a notice on that Monday is still sendable: the content reads the same span.
    slack = FakeSlack()
    assert (
        send_meeting_notice(session, slack, KIM, now=datetime(2026, 10, 12, 0, 0, tzinfo=UTC))  # type: ignore[arg-type]
        is True
    )


# --- the task ----------------------------------------------------------------------


def test_the_setting_is_off_by_default_and_the_task_then_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ExtractionSettings(_env_file=None).after_meeting_notice is False  # type: ignore[call-arg]
    monkeypatch.setattr(tasks, "get_settings", lambda: SimpleNamespace(after_meeting_notice=False))
    monkeypatch.setattr(tasks, "session_scope", None)  # would fail if it were opened

    assert tasks.send_meeting_notices() == 0


def test_the_notice_is_a_periodic_task() -> None:
    assert tasks.send_meeting_notices.name == "autune.extraction.periodic.send_meeting_notices"


@dataclass
class _Config:
    secret: str

    def require_secret(self) -> str:
        return self.secret


class _Clock(datetime):
    moment = AT_10

    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def, override]
        return cls.moment


@pytest.fixture
def checked_slack(session: Session, monkeypatch: pytest.MonkeyPatch) -> CheckedSlack:
    """The task with this session, connected teams, the switch on, Wednesday
    10:00 in Korea, and the fake that runs the outbound check."""

    @contextmanager
    def scope() -> Iterator[Session]:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise

    fake = CheckedSlack()
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "load_integration", lambda s, team, service: _Config("xoxb"))
    monkeypatch.setattr(tasks, "SlackClient", lambda secret: fake)
    monkeypatch.setattr(tasks, "datetime", _Clock)
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None, after_meeting_notice=True),  # type: ignore[call-arg]
    )
    _Clock.moment = AT_10
    return fake


def test_the_task_tells_each_person_alone_and_once(
    session: Session, checked_slack: CheckedSlack
) -> None:
    item(session, "act_1")
    item(session, "act_lee", who="user_lee")
    session.commit()

    # A count: what a task returns is kept by the result backend, and a list
    # of who was told would be a record of who was given work in which run.
    assert tasks.send_meeting_notices() == 2
    assert tasks.send_meeting_notices() == 0

    assert sorted(m.channel for m in checked_slack.sent) == ["user_kim", "user_lee"]
    assert all(m.is_dm for m in checked_slack.sent)
    assert not any(SECRET_TEXT in m.text for m in checked_slack.sent)


def test_a_refused_notice_is_raised_after_the_others_go_and_not_tried_again(
    session: Session, checked_slack: CheckedSlack
) -> None:
    """A meeting's title is a value somebody typed; here it holds a phone
    number. That person's notice is refused, the other's goes, the refusal is
    raised and its claim kept, so the next run does not refuse it again."""
    session.get(Meeting, "mtg_team_2").title = "010-1234-5678 고객 통화"  # type: ignore[union-attr]
    item(session, "act_other", meeting="mtg_team_2")
    item(session, "act_lee", who="user_lee")
    session.commit()

    with pytest.raises(PrivacyViolationError, match="mtg_team_2") as raised:
        tasks.send_meeting_notices()
    # The refusal names the meeting whose text was refused, and no person: its
    # message goes where the return value goes.
    assert "user_" not in str(raised.value)
    assert "010-1234-5678" not in str(raised.value)

    assert [m.channel for m in checked_slack.sent] == ["user_lee"]
    assert not any("010-1234-5678" in m.text for m in checked_slack.sent)
    assert sorted((n.meeting_id, n.user_id) for n in session.query(ExtMeetingNotice)) == [
        ("mtg_team_1", "user_lee"),
        ("mtg_team_2", "user_kim"),  # refused, settled
    ]
    assert tasks.send_meeting_notices() == 0, "nothing owed, nothing refused, nothing raised"


def test_a_refused_read_of_a_calendar_is_raised_by_meeting_too_and_claims_nothing(
    session: Session, checked_slack: CheckedSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where out-of-office is read, the question to a person's calendar can be
    refused instead of the message. That refusal goes where the other goes --
    into the result backend -- so it names the meeting and no person either;
    nothing was refused of the notice itself, so nothing is claimed for it."""
    item(session, "act_1")
    session.commit()
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: ExtractionSettings(  # type: ignore[call-arg]
            _env_file=None, after_meeting_notice=True, leave_from_calendar=True
        ),
    )

    def refused(_would_go: object, _user_id: str, _now: datetime) -> bool:
        raise PrivacyViolationError("the calendar read was refused")

    monkeypatch.setattr(tasks, "_held_back", refused)

    with pytest.raises(PrivacyViolationError, match="mtg_team_1") as raised:
        tasks.send_meeting_notices()

    assert "user_" not in str(raised.value)
    assert checked_slack.sent == []
    assert session.query(ExtMeetingNotice).count() == 0


def test_a_notice_slack_did_not_take_stays_owed(
    session: Session, checked_slack: CheckedSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(session, "act_1")
    session.commit()

    def down(user_id: str, text: str, blocks=None) -> str:  # type: ignore[no-untyped-def]
        raise TransientIntegrationError("slack timed out")

    monkeypatch.setattr(checked_slack, "send_dm", down)

    assert tasks.send_meeting_notices() == 0

    assert session.query(ExtMeetingNotice).count() == 0
    assert owed(session) == [("mtg_team_1", "user_kim")]
