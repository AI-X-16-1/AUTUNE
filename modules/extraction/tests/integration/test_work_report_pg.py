"""The work-report draft, on PostgreSQL.

What SQLite cannot show: "today" compares a ``timestamptz`` edit -- written by
the board's own ``update_action_item``, with the database's clock -- against
Korea's midnight; the once-a-day claim is a real ``ON CONFLICT DO NOTHING`` on
(person, team, day); the table the migration made is the one the model
describes; and its rows go with the account and with the team.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, Team, TeamMember, User
from autune_extraction import reminders, service, work_report
from autune_extraction.models import ExtActionItem, ExtWorkReport
from autune_extraction.schemas import ActionItemUpdate


class FakeSlack:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_dm(self, user_id: str, text: str) -> str:
        self.sent.append((user_id, text))
        return "17000.1"


def count(session: Session, table: str) -> int:
    return session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608


def test_an_work_report_reads_today_is_claimed_once_and_goes_with_the_account(
    db_session: Session,
) -> None:
    team = Team(name="플랫폼팀")
    user = User(email="report@example.com", display_name="담당자")
    other = User(email="report-other@example.com", display_name="다른 사람")
    db_session.add_all([team, user, other])
    db_session.flush()
    db_session.add_all(
        [
            TeamMember(team_id=team.id, user_id=user.id),
            TeamMember(team_id=team.id, user_id=other.id),
        ]
    )
    meeting = Meeting(team_id=team.id, title="주간 회의")
    db_session.add(meeting)
    db_session.flush()

    now = datetime.now(tz=UTC)
    day = reminders.korean_day(now)

    def item(text: str, who: str, status: str = "todo", **extra: object) -> ExtActionItem:
        row = ExtActionItem(
            meeting_id=meeting.id,
            description=text,
            assignee_id=who,
            status=status,
            confidence=0.9,
            origin="model",
            **extra,
        )
        db_session.add(row)
        db_session.flush()
        return row

    finished = item("보고서 쓰기", user.id)
    started = item("배포 점검", user.id)
    item("전부터 하던 일", user.id, status="in_progress")
    item("늦은 일", user.id, due_date=day - timedelta(days=2))
    theirs = item("다른 사람 일", other.id)

    owed = work_report.WorkReportOwed(user_id=user.id, team_id=team.id, day=day)
    slack = FakeSlack()
    assert work_report.send_report(db_session, slack, owed, now=now) is False, (
        "nothing of today yet"
    )
    assert count(db_session, "ext_work_reports") == 0

    service.update_action_item(db_session, finished, ActionItemUpdate(status=ActionStatus.DONE))
    service.update_action_item(
        db_session, started, ActionItemUpdate(status=ActionStatus.IN_PROGRESS)
    )
    service.update_action_item(db_session, theirs, ActionItemUpdate(status=ActionStatus.DONE))
    db_session.flush()

    report = work_report.report_content(db_session, owed, now=datetime.now(tz=UTC))
    assert [line.description for line in report.done] == ["보고서 쓰기"]
    assert [line.description for line in report.moved] == ["배포 점검"]
    assert [line.description for line in report.carried] == ["전부터 하던 일"]
    assert [line.description for line in report.late] == ["늦은 일"]

    assert work_report.send_report(db_session, slack, owed, now=now) is True
    assert work_report.send_report(db_session, slack, owed, now=now) is False
    ((to, text),) = slack.sent
    assert to == user.id
    assert "플랫폼팀 업무 보고" in text and "다른 사람 일" not in text
    assert count(db_session, "ext_work_reports") == 1
    assert count(db_session, "ext_daily_digests") == 0, "the morning DM's table is not touched"

    work_report.settle_refused(db_session, owed, now=now)
    assert count(db_session, "ext_work_reports") == 1, "settling a claimed day adds nothing"

    db_session.execute(sa.delete(User).where(User.id == user.id))
    db_session.flush()
    assert db_session.scalars(sa.select(ExtWorkReport)).all() == []
