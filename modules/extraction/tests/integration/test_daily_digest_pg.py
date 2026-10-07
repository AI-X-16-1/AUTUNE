"""The morning DM and a person's leave dates, on PostgreSQL.

What SQLite cannot show: the once-a-day claim is a real ``ON CONFLICT DO
NOTHING``; "since the last one" compares ``timestamptz`` values; the database
itself refuses a range that ends before it starts; and both tables go with
the account -- a person who deletes their data leaves no dates behind.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, Team, TeamMember, User
from autune_core.errors import ConflictError
from autune_extraction import leave_calendar, service
from autune_extraction.models import (
    ExtActionItem,
    ExtCalendarCleanup,
    ExtDailyDigest,
    ExtEditEvent,
    ExtNotificationPause,
)
from autune_extraction.schemas import ActionItemUpdate
from autune_integrations import TransientIntegrationError

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


def test_a_draft_confirmed_since_the_last_one_is_newly_held(
    db_session: Session, person: dict[str, str]
) -> None:
    """Review of #833, on the database that compares the event times for real:
    a model's draft has no ``created`` event, and its confirmation is its first
    status edit."""
    meeting_id = db_session.scalars(sa.select(Meeting.id)).one()
    fresh, old = (
        ExtActionItem(
            meeting_id=meeting_id,
            description=text,
            assignee_id=person["user"],
            status="needs_confirmation",
            confidence=0.9,
            origin="model",
        )
        for text in ("어제 확인한 일", "지난달에 확인한 일")
    )
    db_session.add_all([fresh, old])
    db_session.flush()
    for item, status, at in (
        (old, ActionStatus.TODO, datetime(2026, 9, 1, tzinfo=UTC)),
        (old, ActionStatus.IN_PROGRESS, MONDAY_NOON_KST),
        (fresh, ActionStatus.TODO, MONDAY_NOON_KST),
    ):
        before = set(db_session.scalars(sa.select(ExtEditEvent.id)))
        service.update_action_item(db_session, item, ActionItemUpdate(status=status))
        db_session.flush()
        (event,) = [e for e in db_session.query(ExtEditEvent) if e.id not in before]
        event.created_at = at
        db_session.flush()
    slack = FakeSlack()
    (owed,) = service.daily_digests_to_send(db_session, now=TUESDAY_10_KST)

    service.send_daily_digest(db_session, slack, owed, now=TUESDAY_10_KST)

    text = slack.sent[0][1]
    assert "• 새로 맡음: 어제 확인한 일 · 주간 회의" in text
    assert "새로 맡음: 지난달에 확인한 일" not in text


def test_a_leave_on_the_persons_calendar_is_one_row_and_one_event_on_postgres(
    db_session: Session, person: dict[str, str]
) -> None:
    """``leave_calendar`` on the real database: the pause is claimed and locked
    (``ON CONFLICT`` on its key, ``FOR UPDATE``), so a second save moves the
    event the first made; a removal Google refuses is queued once."""

    class Calendar:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.down = False

        def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
            self.calls.append((method, path.rsplit("/", 1)[1]))
            return {"id": "evt_1"} if method == "POST" else {"status": "confirmed"}

        def delete_event(self, calendar_id: str, event_id: str) -> None:
            if self.down:
                raise TransientIntegrationError("down")
            self.calls.append(("DELETE", event_id))

    calendar = Calendar()

    def save(first: Any, last: Any, *, on_calendar: bool) -> str:
        return leave_calendar.set_leave(
            db_session,
            lambda _user: (calendar, "primary"),
            person["user"],
            starts_on=first,
            ends_on=last,
            on_calendar=on_calendar,
            now=TUESDAY_10_KST,
        )

    assert save(TUESDAY, TUESDAY + timedelta(days=2), on_calendar=True) == "added"
    assert (
        save(TUESDAY + timedelta(days=1), TUESDAY + timedelta(days=3), on_calendar=True) == "added"
    )

    assert calendar.calls == [("POST", "events"), ("PATCH", "evt_1")]
    (row,) = db_session.scalars(sa.select(ExtNotificationPause)).all()
    assert (row.calendar_event_id, row.starts_on) == ("evt_1", TUESDAY + timedelta(days=1))

    calendar.down = True
    assert save(None, None, on_calendar=False) == "removal_queued"
    assert save(None, None, on_calendar=False) == "off", "nothing left to remove"
    (queued,) = db_session.scalars(sa.select(ExtCalendarCleanup)).all()
    assert (queued.user_id, queued.event_id) == (person["user"], "evt_1")


def test_a_save_at_google_holds_no_lock_and_keeps_a_second_save_out(
    db_engine: sa.Engine,
) -> None:
    """Two requests at once, each on its own connection, for real: committed
    rows, not this file's rolled-back session. While the first is waiting on
    Google its dates are already committed and its row is not locked -- a
    ``FOR UPDATE NOWAIT`` from the other connection gets it -- and the second
    save is refused by the claim instead of making a second event
    (mminjae97's review of #922)."""
    with Session(db_engine) as setup:
        user = User(email=f"leave-{uuid.uuid4().hex}@example.com", display_name="휴가")
        setup.add(user)
        setup.commit()
        user_id = user.id

    at_google, answered = threading.Event(), threading.Event()

    class Slow:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
            self.calls.append(method)
            at_google.set()
            assert answered.wait(timeout=30), "nobody let Google answer"
            return {"id": "evt_1"}

        def delete_event(self, calendar_id: str, event_id: str) -> None:
            self.calls.append("DELETE")

    slow, other = Slow(), Slow()
    first: dict[str, Any] = {}

    def press() -> None:
        try:
            with Session(db_engine) as session:
                first["outcome"] = leave_calendar.set_leave(
                    session,
                    lambda _user: (slow, "primary"),
                    user_id,
                    starts_on=TUESDAY,
                    ends_on=TUESDAY + timedelta(days=2),
                    on_calendar=True,
                    now=TUESDAY_10_KST,
                )
                session.commit()
        except BaseException as exc:  # noqa: BLE001 -- shown by the assert below
            first["error"] = exc

    thread = threading.Thread(target=press)
    thread.start()
    try:
        assert at_google.wait(timeout=30), first.get("error")
        with Session(db_engine) as second:
            row = second.execute(
                sa.select(ExtNotificationPause)
                .where(ExtNotificationPause.user_id == user_id)
                .with_for_update(nowait=True)
            ).scalar_one()
            assert (row.starts_on, row.calendar_event_id) == (TUESDAY, None)
            assert row.calendar_claimed_at == TUESDAY_10_KST
            second.rollback()

            with pytest.raises(ConflictError):
                leave_calendar.set_leave(
                    second,
                    lambda _user: (other, "primary"),
                    user_id,
                    starts_on=TUESDAY + timedelta(days=1),
                    ends_on=TUESDAY + timedelta(days=3),
                    on_calendar=True,
                    now=TUESDAY_10_KST + timedelta(seconds=1),
                )
    finally:
        answered.set()
        thread.join(timeout=30)
        with Session(db_engine) as after:
            kept = after.get(ExtNotificationPause, user_id)
            state = (
                None
                if kept is None
                else (kept.starts_on, kept.calendar_event_id, kept.calendar_claimed_at)
            )
            after.execute(sa.delete(User).where(User.id == user_id))
            after.commit()

    assert first == {"outcome": "added"}
    assert (slow.calls, other.calls) == (["POST"], [])
    assert state == (TUESDAY, "evt_1", None)
