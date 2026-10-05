"""The morning DM and a person's leave dates, on PostgreSQL.

What SQLite cannot show: the once-a-day claim is a real ``ON CONFLICT DO
NOTHING``; "since the last one" compares ``timestamptz`` values; the database
itself refuses a range that ends before it starts; and both tables go with
the account -- a person who deletes their data leaves no dates behind.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_core import Meeting, Team, TeamMember, User
from autune_extraction import service
from autune_extraction.models import ExtActionItem, ExtDailyDigest, ExtEditEvent

TUESDAY = date(2026, 10, 6)
TUESDAY_10_KST = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)
MONDAY_NOON_KST = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)


class FakeSlack:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_dm(self, user_id: str, text: str) -> str:
        self.sent.append((user_id, text))
        return "17000.1"


def count(session: Session, table: str) -> int:
    return session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608


@pytest.fixture
def person(db_session: Session) -> dict[str, str]:
    """A team member with one open item due today and one finished yesterday."""
    team = Team(name="팀")
    user = User(email="assignee@example.com", display_name="담당자")
    db_session.add_all([team, user])
    db_session.flush()
    db_session.add(TeamMember(team_id=team.id, user_id=user.id))
    meeting = Meeting(team_id=team.id, title="주간 회의")
    db_session.add(meeting)
    db_session.flush()
    due = ExtActionItem(
        meeting_id=meeting.id,
        description="오늘 일",
        assignee_id=user.id,
        status="todo",
        due_date=TUESDAY,
        confidence=0.9,
        origin="model",
    )
    finished, long_done = (
        ExtActionItem(
            meeting_id=meeting.id,
            description=text,
            assignee_id=user.id,
            status="done",
            confidence=0.9,
            origin="model",
        )
        for text in ("어제 끝낸 일", "오래전에 끝낸 일")
    )
    db_session.add_all([due, finished, long_done])
    db_session.flush()
    for item, at in ((finished, MONDAY_NOON_KST), (long_done, datetime(2026, 9, 1, tzinfo=UTC))):
        db_session.add(
            ExtEditEvent(
                meeting_id=meeting.id,
                action_item_id=item.id,
                kind="edited",
                fields="status",
                created_at=at,
            )
        )
    db_session.flush()
    return {"user": user.id, "team": team.id}


def test_the_morning_dm_goes_once_and_counts_from_the_day_before(
    db_session: Session, person: dict[str, str]
) -> None:
    slack = FakeSlack()
    (owed,) = service.daily_digests_to_send(db_session, now=TUESDAY_10_KST)

    assert service.send_daily_digest(db_session, slack, owed, now=TUESDAY_10_KST) is True
    assert service.send_daily_digest(db_session, slack, owed, now=TUESDAY_10_KST) is False

    ((who, text),) = slack.sent
    assert who == person["user"]
    assert "• 완료: 어제 끝낸 일 · 주간 회의" in text
    assert "오래전에 끝낸 일" not in text
    assert "• 오늘 기한: 오늘 일 · 주간 회의" in text
    assert service.daily_digests_to_send(db_session, now=TUESDAY_10_KST) == []
    assert count(db_session, "ext_daily_digests") == 1


def test_the_next_one_counts_from_the_stored_time_of_the_last(
    db_session: Session, person: dict[str, str]
) -> None:
    """Yesterday's DM went after the item was finished: it is not news again."""
    db_session.add(
        ExtDailyDigest(
            user_id=person["user"],
            team_id=person["team"],
            day=TUESDAY - timedelta(days=1),
            sent_at=MONDAY_NOON_KST + timedelta(hours=1),
        )
    )
    db_session.flush()
    slack = FakeSlack()
    (owed,) = service.daily_digests_to_send(db_session, now=TUESDAY_10_KST)

    service.send_daily_digest(db_session, slack, owed, now=TUESDAY_10_KST)

    assert "어제 끝낸 일" not in slack.sent[0][1]
    assert "• 바뀐 것이 없습니다." in slack.sent[0][1]


def test_a_paused_day_is_owed_nothing_and_the_dates_go_with_the_account(
    db_session: Session, person: dict[str, str]
) -> None:
    service.set_notification_pause(
        db_session,
        person["user"],
        starts_on=TUESDAY,
        ends_on=TUESDAY + timedelta(days=3),
        now=TUESDAY_10_KST,
    )
    db_session.add(
        ExtDailyDigest(
            user_id=person["user"],
            team_id=person["team"],
            day=TUESDAY - timedelta(days=4),
            sent_at=TUESDAY_10_KST - timedelta(days=4),
        )
    )
    db_session.flush()

    assert service.daily_digests_to_send(db_session, now=TUESDAY_10_KST) == []

    db_session.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": person["user"]})

    assert count(db_session, "ext_notification_pauses") == 0, "when they were away goes too"
    assert count(db_session, "ext_daily_digests") == 0


def test_a_team_that_goes_takes_its_sent_marks(db_session: Session, person: dict[str, str]) -> None:
    db_session.add(
        ExtDailyDigest(
            user_id=person["user"], team_id=person["team"], day=TUESDAY, sent_at=TUESDAY_10_KST
        )
    )
    db_session.flush()

    db_session.execute(sa.text("DELETE FROM teams WHERE id = :id"), {"id": person["team"]})

    assert count(db_session, "ext_daily_digests") == 0


def test_the_database_refuses_a_range_that_ends_before_it_starts(
    db_session: Session, person: dict[str, str]
) -> None:
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(
            sa.text(
                "INSERT INTO ext_notification_pauses (user_id, starts_on, ends_on, created_at) "
                "VALUES (:user, :first, :last, :now)"
            ),
            {
                "user": person["user"],
                "first": TUESDAY,
                "last": TUESDAY - timedelta(days=1),
                "now": TUESDAY_10_KST,
            },
        )
    assert count(db_session, "ext_notification_pauses") == 0
