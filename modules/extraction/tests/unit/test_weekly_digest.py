"""Monday's DM of a person's own open items (the user, 2026-10-04).

The rules under test: only on a Monday's sending hours in Korea; one per
person and team, once a week; only the person's own open items, read again at
send time; nothing to someone with nothing left; most urgent first, escaped,
at most ten lines.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autune_core import Base, Meeting, Team, TeamMember, User
from autune_extraction import reminders, service
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem, ExtWeeklyDigest
from autune_extraction.reminders import DigestLine, build_weekly_digest, digest_week

MONDAY_10_KST = datetime(2026, 10, 5, 1, 0, tzinfo=UTC)  # 2026-10-05 is a Monday
MONDAY = date(2026, 10, 5)


def test_a_digest_goes_only_on_a_mondays_sending_hours_in_korea() -> None:
    assert digest_week(MONDAY_10_KST) == MONDAY
    assert digest_week(datetime(2026, 10, 5, 12, 0, tzinfo=UTC)) is None  # 21:00 KST
    assert digest_week(datetime(2026, 10, 6, 1, 0, tzinfo=UTC)) is None  # Tuesday


def test_the_message_puts_the_late_first_escapes_and_stops_at_ten() -> None:
    lines = [
        DigestLine("다음 달 일", date(2026, 11, 20), None),
        DigestLine("늦은 일 <!channel>", date(2026, 10, 1), "주간 회의"),
        DigestLine("이번 주 일", date(2026, 10, 8), None),
        *[DigestLine(f"기한 없는 일 {n}", None, None) for n in range(10)],
    ]

    text = build_weekly_digest(lines, today=MONDAY, board_url="https://autune.example/actions")
    body = text.split("\n")

    assert body[0] == "이번 주 열린 할 일 13개입니다."
    assert body[1] == "• 늦은 일 &lt;!channel&gt; · 기한 지남(10월 1일 목) · 주간 회의"
    assert body[2] == "• 이번 주 일 · 이번 주 10월 8일 목"
    assert body[3] == "• 다음 달 일 · 기한 11월 20일 금"
    assert body[-2] == "외 3개"
    assert body[-1] == "https://autune.example/actions"


class FakeSlack:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_dm(self, user_id: str, text: str) -> str:
        self.sent.append((user_id, text))
        return "17000.1"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )
    # No public holidays here: the Monday these tests use, 2026-10-05, is one
    # (the substitute day for 개천절). Holidays are test_days_off.py's.
    monkeypatch.setattr(service.days_off, "is_public_holiday", lambda *_, **__: False)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    shared = {m.__tablename__ for m in (Meeting, User, Team, TeamMember)}
    tables = [
        t
        for name, t in Base.metadata.tables.items()
        if name in shared
        or name
        in (
            "ext_action_items",
            "ext_weekly_digests",
            "ext_due_reminder_optouts",
            "ext_notification_pauses",
        )
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        for team in ("team_1", "team_2"):
            s.add(Team(id=team, name=team))
            s.add(Meeting(id=f"mtg_{team}", team_id=team, title=f"{team} 회의"))
        s.add(TeamMember(team_id="team_1", user_id="user_kim"))
        s.add(TeamMember(team_id="team_2", user_id="user_kim"))
        s.add(TeamMember(team_id="team_1", user_id="user_lee"))
        rows = [
            ("act_1", "mtg_team_1", "user_kim", "todo", "로그인 고치기"),
            ("act_2", "mtg_team_2", "user_kim", "in_progress", "다른 팀 일"),
            ("act_3", "mtg_team_1", "user_lee", "done", "끝난 일"),
            ("act_4", "mtg_team_1", "user_gone", "todo", "팀에 없는 사람 일"),
            ("act_5", "mtg_team_1", "user_lee", "needs_confirmation", "확인 전 일"),
        ]
        for item_id, meeting, who, status, text in rows:
            s.add(
                ExtActionItem(
                    id=item_id,
                    meeting_id=meeting,
                    description=text,
                    assignee_id=who,
                    status=status,
                    confidence=0.9,
                    origin="model",
                )
            )
        s.flush()
        yield s


def test_a_date_says_its_year_only_when_it_is_not_the_digests() -> None:
    """The user, 2026-10-09: a due date as B's screens write one."""
    last_monday_of_the_year = date(2026, 12, 28)
    lines = [
        DigestLine("지난해 일", date(2025, 12, 31), None),
        DigestLine("연말 일", date(2026, 12, 30), None),
        DigestLine("새해 첫 주 일", date(2027, 1, 2), None),
        DigestLine("새해 일", date(2027, 1, 4), None),
    ]

    body = build_weekly_digest(lines, today=last_monday_of_the_year, board_url="u").split("\n")

    assert body[1:5] == [
        "• 지난해 일 · 기한 지남(2025년 12월 31일 수)",
        "• 연말 일 · 이번 주 12월 30일 수",
        "• 새해 첫 주 일 · 이번 주 2027년 1월 2일 토",
        "• 새해 일 · 기한 2027년 1월 4일 월",
    ]


def test_one_digest_is_owed_per_person_and_team_with_open_work(session: Session) -> None:
    owed = service.weekly_digests_to_send(session, now=MONDAY_10_KST)

    assert [(d.user_id, d.team_id) for d in owed] == [
        ("user_kim", "team_1"),
        ("user_kim", "team_2"),
    ]
    assert (
        service.weekly_digests_to_send(session, now=datetime(2026, 10, 6, 1, 0, tzinfo=UTC)) == []
    )


def test_a_digest_goes_to_its_person_once_a_week(session: Session) -> None:
    slack = FakeSlack()
    (first, _) = service.weekly_digests_to_send(session, now=MONDAY_10_KST)

    assert service.send_weekly_digest(session, slack, first, now=MONDAY_10_KST) is True
    assert service.send_weekly_digest(session, slack, first, now=MONDAY_10_KST) is False

    assert len(slack.sent) == 1
    who, text = slack.sent[0]
    assert who == "user_kim"
    assert "로그인 고치기" in text and "다른 팀 일" not in text, "one team's bot, its own work"
    assert [
        (d.user_id, d.team_id) for d in service.weekly_digests_to_send(session, now=MONDAY_10_KST)
    ] == [("user_kim", "team_2")]


def test_someone_whose_work_is_done_by_send_time_gets_nothing(session: Session) -> None:
    slack = FakeSlack()
    (first, _) = service.weekly_digests_to_send(session, now=MONDAY_10_KST)
    item = session.get(ExtActionItem, "act_1")
    assert item is not None
    item.status = "done"
    session.flush()

    assert service.send_weekly_digest(session, slack, first, now=MONDAY_10_KST) is False
    assert slack.sent == []
    assert session.query(ExtWeeklyDigest).count() == 0


# --- "마감 알림 받기" off means this DM too (follow-up to #771) -------------------------


def test_someone_who_turned_their_reminders_off_is_owed_no_digest(session: Session) -> None:
    """One switch for Autune's DMs about a person's own items: someone who
    turned the reminders off still got Monday's list of the same items."""
    service.set_due_reminders(session, "user_kim", on=False, now=MONDAY_10_KST)

    assert service.weekly_digests_to_send(session, now=MONDAY_10_KST) == []

    service.set_due_reminders(session, "user_kim", on=True, now=MONDAY_10_KST)

    assert [
        (d.user_id, d.team_id) for d in service.weekly_digests_to_send(session, now=MONDAY_10_KST)
    ] == [("user_kim", "team_1"), ("user_kim", "team_2")]


def test_turning_them_off_does_not_cost_anyone_else_their_digest(session: Session) -> None:
    lee = session.get(ExtActionItem, "act_3")
    assert lee is not None
    lee.status = "todo"
    session.flush()
    service.set_due_reminders(session, "user_kim", on=False, now=MONDAY_10_KST)

    owed = service.weekly_digests_to_send(session, now=MONDAY_10_KST)

    assert [(d.user_id, d.team_id) for d in owed] == [("user_lee", "team_1")]


def test_someone_who_turns_them_off_before_the_send_gets_nothing(session: Session) -> None:
    """The list was made in another transaction; the person's choice is read
    again at send time, as their items are."""
    slack = FakeSlack()
    (first, _) = service.weekly_digests_to_send(session, now=MONDAY_10_KST)
    service.set_due_reminders(session, "user_kim", on=False, now=MONDAY_10_KST)

    assert service.send_weekly_digest(session, slack, first, now=MONDAY_10_KST) is False
    assert slack.sent == []
    assert session.query(ExtWeeklyDigest).count() == 0, "nothing claimed: on again, it is owed"


def test_the_setting_is_off_by_default() -> None:
    assert ExtractionSettings(_env_file=None).weekly_digest is False  # type: ignore[call-arg]
    assert reminders.DIGEST_MAX_LINES == 10


def test_a_row_with_a_short_title_is_named_by_it_in_the_digest(session: Session) -> None:
    """Module B's owner, 2026-10-09: "Slack·회의록까지 전부" -- a line of a
    message is the title alone, with no kind mark ("붙이지 않기")."""
    row = session.get(ExtActionItem, "act_1")
    assert row is not None
    row.title = "로그인 수정"
    session.flush()
    slack = FakeSlack()
    (first, _) = service.weekly_digests_to_send(session, now=MONDAY_10_KST)

    assert service.send_weekly_digest(session, slack, first, now=MONDAY_10_KST) is True

    _, text = slack.sent[0]
    assert "• 로그인 수정" in text
    assert "로그인 고치기" not in text and "[할 일]" not in text
