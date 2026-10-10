"""``tools.may_notify_now``: whether Autune may DM a person about work right now
(#1046) -- yes or no, never why."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, User
from autune_extraction import meeting_notice, tools
from autune_extraction.models import ExtDueReminderOptOut, ExtNotificationPause

# Wednesday 2026-10-07, 10:00 in Korea.
WED_10 = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_holidays(monkeypatch: pytest.MonkeyPatch) -> None:
    # The week has a real holiday on its Friday (한글날); a test that wants
    # one names it.
    monkeypatch.setattr(meeting_notice.days_off, "is_public_holiday", lambda *_, **__: False)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    tables = [
        t
        for name, t in Base.metadata.tables.items()
        if name == User.__tablename__ or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(User(id="user_kim", email="kim@example.com", display_name="kim"))
        s.flush()
        yield s


def test_yes_on_a_working_day_inside_the_hours(session: Session) -> None:
    assert tools.may_notify_now(session, "user_kim", now=WED_10) is True


def test_no_for_someone_who_turned_their_reminders_off(session: Session) -> None:
    session.add(ExtDueReminderOptOut(user_id="user_kim", created_at=WED_10))
    session.flush()

    assert tools.may_notify_now(session, "user_kim", now=WED_10) is False


def test_no_inside_their_own_leave_and_yes_the_day_after(session: Session) -> None:
    session.add(
        ExtNotificationPause(
            user_id="user_kim",
            starts_on=date(2026, 10, 6),
            ends_on=date(2026, 10, 7),
            created_at=WED_10,
        )
    )
    session.flush()

    assert tools.may_notify_now(session, "user_kim", now=WED_10) is False
    assert tools.may_notify_now(session, "user_kim", now=WED_10 + timedelta(days=1)) is True


def test_the_leave_day_is_koreas_not_the_servers(session: Session) -> None:
    """00:30 UTC on the 8th is 09:30 in Korea on the 8th; a pause ending on
    the 7th has ended, though the server's date would say otherwise at 23:30
    UTC on the 7th (08:30 Korea, before the hours anyway)."""
    session.add(
        ExtNotificationPause(
            user_id="user_kim",
            starts_on=date(2026, 10, 7),
            ends_on=date(2026, 10, 7),
            created_at=WED_10,
        )
    )
    session.flush()

    assert tools.may_notify_now(session, "user_kim", now=datetime(2026, 10, 8, 0, 30, tzinfo=UTC))


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 10, 6, 23, 59, tzinfo=UTC),  # 08:59 Wednesday in Korea
        datetime(2026, 10, 7, 8, 0, tzinfo=UTC),  # 17:00 Wednesday in Korea
        datetime(2026, 10, 10, 1, 0, tzinfo=UTC),  # 10:00 Saturday in Korea
    ],
)
def test_no_outside_nine_to_five_on_a_working_day(session: Session, now: datetime) -> None:
    assert tools.may_notify_now(session, "user_kim", now=now) is False


def test_no_on_a_public_holiday(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        meeting_notice.days_off,
        "is_public_holiday",
        lambda _session, day, **__: day == date(2026, 10, 7),
    )

    assert tools.may_notify_now(session, "user_kim", now=WED_10) is False


def test_it_is_not_offered_to_the_model() -> None:
    assert tools.may_notify_now not in tools.TOOLS
    assert tools.may_notify_now not in tools.ACTIONS
