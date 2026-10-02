"""Due-date reminders (the user, 2026-10-02): the assignee is told once, the day
before and after a miss, by direct message, and nobody else is told anything.

SQLite in memory, ``FakeSlack`` behind the team's Slack connection, the task
run with a fixed clock. What is under test: which items are owed a reminder,
that it goes to the assignee alone and once, when it may be sent, and that a
failed send is tried again.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import Base, Meeting, PrivacyViolationError, TeamMember, User
from autune_extraction import reminders, service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem, ExtDueReminder
from autune_integrations.errors import SlackRecipientNotLinkedError, TransientIntegrationError
from autune_integrations.fakes import FakeSlack

MEETING = "mtg_1"
TEAM = "team_1"
KIM, PARK, GONE = "user_kim", "user_park", "user_gone"

# 10:00 in Korea on Friday 2026-10-02.
NOW = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
TODAY = date(2026, 10, 2)
TOMORROW = TODAY + timedelta(days=1)
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id=TEAM, title="주간 회의"))
        for user_id, name in ((KIM, "김민경"), (PARK, "박재경"), (GONE, "떠난 사람")):
            s.add(User(id=user_id, email=f"{user_id}@example.com", display_name=name))
        s.add(TeamMember(team_id=TEAM, user_id=KIM))
        s.add(TeamMember(team_id=TEAM, user_id=PARK))
        s.add(TeamMember(team_id="team_other", user_id=GONE))
        s.flush()
        yield s


@dataclass
class _Config:
    secret: str

    def require_secret(self) -> str:
        return self.secret


class _Clock(datetime):
    """``datetime.now`` as the task calls it, held at a moment a test picks."""

    moment = NOW

    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def, override]
        return cls.moment


@pytest.fixture
def slack(session: Session, monkeypatch: pytest.MonkeyPatch) -> FakeSlack:
    """The task with this session, a connected team, a fake Slack, and 10:00 in
    Korea on 2026-10-02."""

    @contextmanager
    def scope() -> Iterator[Session]:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise

    fake = FakeSlack()
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "load_integration", lambda s, team, service: _Config("xoxb"))
    monkeypatch.setattr(tasks, "SlackClient", lambda secret: fake)
    monkeypatch.setattr(tasks, "datetime", _Clock)
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )
    _Clock.moment = NOW
    return fake


def item(
    session: Session,
    item_id: str,
    *,
    due: date | None = TOMORROW,
    assignee: str | None = KIM,
    status: str = "todo",
    description: str = "스펙 초안 공유",
) -> ExtActionItem:
    row = ExtActionItem(
        id=item_id,
        meeting_id=MEETING,
        description=description,
        status=status,
        assignee_id=assignee,
        due_date=due,
        confidence=0.9,
        origin="model",
    )
    session.add(row)
    session.commit()
    return row


def reminded(session: Session) -> list[tuple[str, str, date]]:
    return sorted(
        session.execute(
            select(ExtDueReminder.action_item_id, ExtDueReminder.kind, ExtDueReminder.due_date)
        ).all()
    )


# --- which day, which kind -------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "kind"),
    [
        (2, None),
        (1, reminders.DUE_SOON),
        (0, None),
        (-1, reminders.OVERDUE),
        (-3, reminders.OVERDUE),
        (-4, None),
        (-30, None),
    ],
)
def test_a_reminder_is_owed_the_day_before_and_for_three_days_after(
    days: int, kind: str | None
) -> None:
    assert reminders.kind_for(TODAY + timedelta(days=days), TODAY) == kind


@pytest.mark.parametrize(
    ("utc", "day", "open_"),
    [
        (datetime(2026, 10, 1, 23, 59, tzinfo=UTC), date(2026, 10, 2), False),  # 08:59 KST
        (datetime(2026, 10, 2, 0, 0, tzinfo=UTC), date(2026, 10, 2), True),  # 09:00 KST
        (datetime(2026, 10, 2, 10, 59, tzinfo=UTC), date(2026, 10, 2), True),  # 19:59 KST
        (datetime(2026, 10, 2, 11, 0, tzinfo=UTC), date(2026, 10, 2), False),  # 20:00 KST
        (
            datetime(2026, 10, 2, 15, 30, tzinfo=UTC),
            date(2026, 10, 3),
            False,
        ),  # 00:30 KST, next day
    ],
)
def test_days_and_hours_are_koreas(utc: datetime, day: date, open_: bool) -> None:
    assert reminders.korean_day(utc) == day
    assert reminders.sending_hours(utc) is open_


# --- who is told, and what -------------------------------------------------------


def test_the_assignee_is_told_the_day_before_by_direct_message(
    session: Session, slack: FakeSlack
) -> None:
    item(session, "act_1")

    assert tasks.remind_due_items() == ["act_1"]

    (message,) = slack.sent
    assert message.is_dm and message.channel == KIM
    assert message.text == (
        "내일까지인 액션 아이템이 있습니다.\n"
        "• 스펙 초안 공유\n"
        "기한: 2026-10-03 · 회의: 주간 회의\n"
        "http://localhost:3000/meetings/mtg_1/actions"
    )
    assert reminded(session) == [("act_1", "due_soon", TOMORROW)]


def test_the_assignee_is_told_after_the_date_passed(session: Session, slack: FakeSlack) -> None:
    item(session, "act_1", due=YESTERDAY)

    tasks.remind_due_items()

    (message,) = slack.sent
    assert message.channel == KIM
    assert message.text.startswith(
        "기한이 지난 액션 아이템이 있습니다.\n• 스펙 초안 공유\n기한: 2026-10-01"
    )


def test_each_reminder_goes_to_its_own_assignee_and_to_nobody_else(
    session: Session, slack: FakeSlack
) -> None:
    item(session, "act_kim", description="김의 일")
    item(session, "act_park", assignee=PARK, description="박의 일")

    tasks.remind_due_items()

    assert {(m.channel, m.text.splitlines()[1]) for m in slack.sent} == {
        (KIM, "• 김의 일"),
        (PARK, "• 박의 일"),
    }
    assert all(m.is_dm for m in slack.sent)


def test_it_is_said_once(session: Session, slack: FakeSlack) -> None:
    item(session, "act_1")

    tasks.remind_due_items()
    assert tasks.remind_due_items() == []

    assert len(slack.sent) == 1


def test_the_day_before_and_the_day_after_are_two_reminders(
    session: Session, slack: FakeSlack
) -> None:
    item(session, "act_1")
    tasks.remind_due_items()

    _Clock.moment = NOW + timedelta(days=2)  # the day after the due date
    tasks.remind_due_items()
    tasks.remind_due_items()

    assert [m.text.splitlines()[0] for m in slack.sent] == [
        "내일까지인 액션 아이템이 있습니다.",
        "기한이 지난 액션 아이템이 있습니다.",
    ]


def test_a_date_moved_is_a_new_date(session: Session, slack: FakeSlack) -> None:
    row = item(session, "act_1")
    tasks.remind_due_items()

    row.due_date = TOMORROW + timedelta(days=7)
    session.commit()
    _Clock.moment = NOW + timedelta(days=7)
    tasks.remind_due_items()

    assert len(slack.sent) == 2
    assert [due for _, _, due in reminded(session)] == [TOMORROW, TOMORROW + timedelta(days=7)]


# --- who is not told -------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "needs_confirmation"},
        {"status": "done"},
        {"due": None},
        {"due": TODAY},
        {"due": TODAY + timedelta(days=2)},
        {"due": TODAY - timedelta(days=4)},
        {"assignee": None},
        {"assignee": GONE},
    ],
    ids=[
        "not confirmed",
        "done",
        "no date",
        "due today",
        "due in two days",
        "late for four days",
        "a typed name or nobody",
        "an account that is not on the meeting's team",
    ],
)
def test_nothing_is_sent_for(session: Session, slack: FakeSlack, fields: dict) -> None:
    item(session, "act_1", **fields)

    assert tasks.remind_due_items() == []
    assert slack.sent == []
    assert reminded(session) == []


def test_an_expired_meeting_is_left_out_before_the_sweep_removes_it(
    session: Session, slack: FakeSlack
) -> None:
    item(session, "act_1")
    session.get(Meeting, MEETING).expires_at = NOW - timedelta(hours=1)
    session.commit()

    assert tasks.remind_due_items() == []
    assert slack.sent == []


@pytest.mark.parametrize(
    "moment",
    [
        datetime(2026, 10, 1, 23, 30, tzinfo=UTC),  # 08:30 in Korea
        datetime(2026, 10, 2, 12, 0, tzinfo=UTC),  # 21:00 in Korea
    ],
)
def test_nothing_is_sent_outside_koreas_daytime(
    session: Session, slack: FakeSlack, moment: datetime
) -> None:
    item(session, "act_1", due=date(2026, 10, 3))
    _Clock.moment = moment

    assert tasks.remind_due_items() == []
    assert slack.sent == []


def test_a_deployment_can_switch_them_off(
    session: Session, slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(session, "act_1")
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None, due_reminders=False),  # type: ignore[call-arg]
    )

    assert tasks.remind_due_items() == []
    assert slack.sent == []


# --- when Slack is not there -----------------------------------------------------


def test_a_team_without_slack_is_skipped_and_told_once_it_connects(
    session: Session, slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(session, "act_1")
    monkeypatch.setattr(tasks, "load_integration", lambda s, team, service: None)
    assert tasks.remind_due_items() == []
    assert reminded(session) == []

    monkeypatch.setattr(tasks, "load_integration", lambda s, team, service: _Config("xoxb"))
    assert tasks.remind_due_items() == ["act_1"]


def test_someone_who_has_not_linked_slack_is_skipped_without_a_line_each_run(
    session: Session, slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(session, "act_1")
    item(session, "act_2", assignee=PARK)

    def send(user_id: str, text: str, blocks=None) -> str:  # type: ignore[no-untyped-def]
        if user_id == KIM:
            raise SlackRecipientNotLinkedError("not linked")
        return FakeSlack.send_dm(slack, user_id, text, blocks)

    monkeypatch.setattr(slack, "send_dm", send)

    with capture_logs() as logs:
        assert tasks.remind_due_items() == ["act_2"]

    # No claim is kept for the one who could not be reached: still owed.
    assert reminded(session) == [("act_2", "due_soon", TOMORROW)]
    assert [entry["event"] for entry in logs] == ["extraction_due_reminders_sent"]
    assert logs[0]["not_linked"] == 1 and logs[0]["sent"] == 1


def test_a_failed_send_takes_the_claim_back_and_is_tried_again(
    session: Session, slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(session, "act_1")
    working = slack.send_dm

    def down(user_id: str, text: str, blocks=None) -> str:  # type: ignore[no-untyped-def]
        raise TransientIntegrationError("slack timed out")

    monkeypatch.setattr(slack, "send_dm", down)
    assert tasks.remind_due_items() == []
    assert reminded(session) == []

    monkeypatch.setattr(slack, "send_dm", working)
    assert tasks.remind_due_items() == ["act_1"]
    assert len(slack.sent) == 1


def test_a_privacy_violation_is_raised_after_the_others_are_sent(
    session: Session, slack: FakeSlack
) -> None:
    """A description somebody typed a phone number into is refused by the
    outbound check. The other reminder still goes; the refusal is not swallowed
    and leaves no claim."""
    item(session, "act_bad", description="010-1234-5678로 전화하기")
    item(session, "act_ok", assignee=PARK, description="스펙 초안 공유")

    with pytest.raises(PrivacyViolationError, match="act_bad"):
        tasks.remind_due_items()

    assert [m.channel for m in slack.sent] == [PARK]
    assert reminded(session) == [("act_ok", "due_soon", TOMORROW)]


def test_the_log_carries_ids_and_counts_never_the_text(session: Session, slack: FakeSlack) -> None:
    item(session, "act_1", description="고객사 계약 조건 검토")

    with capture_logs() as logs:
        tasks.remind_due_items()

    written = repr(logs)
    assert "고객사" not in written
    assert "주간 회의" not in written
    assert KIM not in written


# --- the claim -------------------------------------------------------------------


def test_a_reminder_another_run_already_claimed_sends_nothing(
    session: Session, slack: FakeSlack
) -> None:
    item(session, "act_1")
    (owed,) = service.due_reminders_to_send(session, now=NOW)
    assert service.send_due_reminder(session, slack, owed, now=NOW) is True
    session.commit()

    # A second run that read the same list before the first one finished.
    assert service.send_due_reminder(session, slack, owed, now=NOW) is False
    assert len(slack.sent) == 1
