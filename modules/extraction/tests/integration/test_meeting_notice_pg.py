"""The notice after a meeting, on PostgreSQL.

What SQLite cannot show: "made since the last working day began" compares
``timestamptz`` values with a bound worked out in Korea time; the once-a-
person-and-meeting claim is a real ``ON CONFLICT DO NOTHING``; and the claim
row goes with its meeting and with its person.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Team, TeamMember, User
from autune_extraction import meeting_notice
from autune_extraction.meeting_notice import NoticeOwed
from autune_extraction.models import ExtActionItem, ExtMeetingNotice

# Wednesday 2026-10-07 in Korea; neither it nor the Tuesday before is a holiday.
AT_10 = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)
AT_17 = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
TUESDAY_0859 = datetime(2026, 10, 5, 23, 59, tzinfo=UTC)
TUESDAY_0900 = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)


class FakeSlack:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_dm(self, user_id: str, text: str) -> str:
        self.sent.append((user_id, text))
        return "17000.1"


def count(session: Session, table: str) -> int:
    return session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608


@pytest.fixture
def landed(db_session: Session) -> dict[str, str]:
    """A member with one draft of a meeting waiting for them, made at 09:50."""
    team = Team(name="팀")
    user = User(email="holder@example.com", display_name="담당자")
    db_session.add_all([team, user])
    db_session.flush()
    db_session.add(TeamMember(team_id=team.id, user_id=user.id))
    meeting = Meeting(team_id=team.id, title="주간 회의")
    db_session.add(meeting)
    db_session.flush()
    item = ExtActionItem(
        meeting_id=meeting.id,
        description="아무도 확인하지 않은 초안",
        assignee_id=user.id,
        status="needs_confirmation",
        due_date=date(2026, 10, 30),
        confidence=0.9,
        origin="model",
        created_at=AT_10 - timedelta(minutes=10),
    )
    db_session.add(item)
    db_session.flush()
    return {"team": team.id, "user": user.id, "meeting": meeting.id, "item": item.id}


def mine(session: Session, ids: dict[str, str], now: datetime) -> list[tuple[str, str]]:
    return [
        (n.meeting_id, n.user_id)
        for n in meeting_notice.notices_to_send(session, now=now)
        if n.meeting_id == ids["meeting"]
    ]


def test_the_span_and_the_hours_are_compared_as_instants(
    db_session: Session, landed: dict[str, str]
) -> None:
    owed = [(landed["meeting"], landed["user"])]
    item = db_session.get(ExtActionItem, landed["item"])
    assert item is not None

    assert mine(db_session, landed, AT_10) == owed
    assert mine(db_session, landed, AT_17) == [], "17:00 in Korea is past the hours"

    # The bound on Wednesday is Tuesday 09:00 in Korea -- midnight UTC.
    item.created_at = TUESDAY_0900
    db_session.flush()
    assert mine(db_session, landed, AT_10) == owed
    item.created_at = TUESDAY_0859
    db_session.flush()
    assert mine(db_session, landed, AT_10) == []


def test_the_claim_is_once_a_person_and_meeting(
    db_session: Session, landed: dict[str, str]
) -> None:
    owed = NoticeOwed(meeting_id=landed["meeting"], team_id=landed["team"], user_id=landed["user"])
    slack = FakeSlack()

    assert meeting_notice.send_meeting_notice(db_session, slack, owed, now=AT_10) is True  # type: ignore[arg-type]
    assert meeting_notice.send_meeting_notice(db_session, slack, owed, now=AT_10) is False  # type: ignore[arg-type]
    meeting_notice.settle_refused_notice(db_session, owed, now=AT_10)  # already there: no error

    ((who, text),) = slack.sent
    assert who == landed["user"]
    assert "잡힌 일 1건이 확인을 기다립니다" in text
    assert "초안" not in text and "2026-10-30" not in text
    assert mine(db_session, landed, AT_10) == []
    row = db_session.get(ExtMeetingNotice, (landed["meeting"], landed["user"]))
    assert row is not None and row.sent_at == AT_10


@pytest.mark.parametrize(
    "statement",
    ["DELETE FROM meetings WHERE id = :meeting", "DELETE FROM users WHERE id = :user"],
    ids=["the meeting", "the person"],
)
def test_the_claim_goes_with_its_meeting_and_with_its_person(
    db_session: Session, landed: dict[str, str], statement: str
) -> None:
    owed = NoticeOwed(meeting_id=landed["meeting"], team_id=landed["team"], user_id=landed["user"])
    meeting_notice.send_meeting_notice(db_session, FakeSlack(), owed, now=AT_10)  # type: ignore[arg-type]
    assert count(db_session, "ext_meeting_notices") >= 1
    db_session.expunge_all()

    db_session.execute(sa.text(statement), {"meeting": landed["meeting"], "user": landed["user"]})

    assert (
        db_session.execute(
            sa.text(
                "SELECT count(*) FROM ext_meeting_notices WHERE meeting_id = :m AND user_id = :u"
            ),
            {"m": landed["meeting"], "u": landed["user"]},
        ).scalar_one()
        == 0
    )
