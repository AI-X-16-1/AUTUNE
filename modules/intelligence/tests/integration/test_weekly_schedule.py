"""When a team's weekly report goes out, and that it goes out once (#227).

A team picks the weekday and hour (KST) its report is sent, Monday 09:00 until
it does, and whether a week with nothing to say is posted at all (not, until
it says so). An hourly task finds each team whose latest slot has passed and
whose report for that slot's week is not yet out, writes it once, and posts it
once.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from autune_core import Meeting, TeamMember, User
from autune_core.errors import PermissionDeniedError, ValidationError
from autune_intelligence import service, tasks
from autune_intelligence.models import IntelReport

KST = service._KST


def _kst(*args: int) -> datetime:
    return datetime(*args, tzinfo=KST)


def _member(db_session: Session, team: str) -> User:
    user = User(email=f"m-{uuid.uuid4().hex}@example.com", display_name="정하는 사람")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


def _active(db_session: Session, team: str) -> None:
    db_session.add(Meeting(team_id=team, title="회의"))
    db_session.flush()


# --- the slot ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "slot"),
    [
        (_kst(2026, 10, 5, 9, 30), _kst(2026, 10, 5, 9)),  # Monday, just after
        (_kst(2026, 10, 5, 8, 59), _kst(2026, 9, 28, 9)),  # Monday, just before: last week's
        (_kst(2026, 10, 8, 14, 0), _kst(2026, 10, 5, 9)),  # Thursday
    ],
)
def test_the_latest_slot_is_the_last_weekday_and_hour_passed(now: datetime, slot: datetime) -> None:
    assert service.latest_weekly_slot(weekday=0, hour=9, now=now) == slot


def test_a_slots_week_is_the_seven_days_before_its_day() -> None:
    assert service.weekly_period(_kst(2026, 10, 7, 18)) == (date(2026, 9, 30), date(2026, 10, 7))


# --- the setting -------------------------------------------------------------------


def test_a_team_without_a_setting_sends_on_monday_at_nine(db_session: Session, team: str) -> None:
    schedule = service.weekly_report_schedule(db_session, team)

    assert (schedule.weekday, schedule.hour, schedule.send_empty) == (0, 9, False)
    assert schedule.updated_by_name is None


def test_a_member_sets_the_day_and_hour_and_is_named(db_session: Session, team: str) -> None:
    user = _member(db_session, team)

    service.set_weekly_report_schedule(
        db_session, team, weekday=2, hour=18, send_empty=True, user_id=user.id
    )
    schedule = service.weekly_report_schedule(db_session, team)

    assert (schedule.weekday, schedule.hour, schedule.send_empty) == (2, 18, True)
    assert schedule.updated_by_name == "정하는 사람"


def test_only_a_member_sets_it(db_session: Session, team: str) -> None:
    outsider = _member(db_session, team)
    db_session.query(TeamMember).filter(TeamMember.user_id == outsider.id).delete()

    with pytest.raises(PermissionDeniedError):
        service.set_weekly_report_schedule(
            db_session, team, weekday=1, hour=9, send_empty=False, user_id=outsider.id
        )


@pytest.mark.parametrize(("weekday", "hour"), [(7, 9), (-1, 9), (0, 24), (0, -1)])
def test_a_day_or_hour_out_of_range_is_refused(
    db_session: Session, team: str, weekday: int, hour: int
) -> None:
    user = _member(db_session, team)

    with pytest.raises(ValidationError):
        service.set_weekly_report_schedule(
            db_session, team, weekday=weekday, hour=hour, send_empty=False, user_id=user.id
        )


# --- what is due -------------------------------------------------------------------


def test_a_team_is_due_once_its_slot_passes_until_its_report_is_out(
    db_session: Session, team: str
) -> None:
    _active(db_session, team)
    before, after = _kst(2026, 10, 5, 8, 30), _kst(2026, 10, 5, 9, 10)
    week = (team, date(2026, 9, 28), date(2026, 10, 5))

    # Last week's slot is more than a day old by then: it is not caught up.
    assert all(t != team for t, _, _ in service.due_weekly_reports(db_session, before))
    assert week in service.due_weekly_reports(db_session, after)

    service.generate_weekly_report(db_session, team, date(2026, 9, 28), date(2026, 10, 5))
    assert week in service.due_weekly_reports(db_session, after)  # written, not yet out

    service.claim_weekly_report_post(db_session, team, date(2026, 9, 28), now=after)
    assert all(t != team for t, _, _ in service.due_weekly_reports(db_session, after))


def test_a_team_with_no_recent_meeting_is_not_sent_an_empty_report(
    db_session: Session, team: str
) -> None:
    assert all(
        t != team for t, _, _ in service.due_weekly_reports(db_session, _kst(2026, 10, 5, 9, 10))
    )


def test_the_teams_own_day_and_hour_decide(db_session: Session, team: str) -> None:
    _active(db_session, team)
    service.set_weekly_report_schedule(
        db_session, team, weekday=2, hour=18, send_empty=True, user_id=_member(db_session, team).id
    )

    monday = service.due_weekly_reports(db_session, _kst(2026, 10, 5, 9, 10))
    wednesday = service.due_weekly_reports(db_session, _kst(2026, 10, 7, 18, 5))

    assert all(t != team for t, _, _ in monday)
    assert (team, date(2026, 9, 30), date(2026, 10, 7)) in wednesday


# --- the hourly task ---------------------------------------------------------------


@pytest.fixture
def use_test_session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextlib.contextmanager
    def _scope() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(tasks, "session_scope", _scope)


@pytest.fixture
def slack(db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    from cryptography.fernet import Fernet

    from autune_core import TeamIntegration, crypto
    from autune_core.crypto import encrypt

    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(crypto, "_fernet", lambda: key)
    db_session.add(
        TeamIntegration(
            team_id=team, service="slack", secret=encrypt("xoxb-test"), config={"channel": "C1"}
        )
    )
    db_session.flush()
    with patch.object(tasks, "SlackClient") as client:
        yield client


@pytest.mark.usefixtures("use_test_session")
def test_the_hourly_task_writes_and_posts_a_due_report_once(
    db_session: Session, team: str, slack: object
) -> None:
    _active(db_session, team)
    now = datetime.now(UTC)
    slot = service.latest_weekly_slot(weekday=0, hour=9, now=now)
    service.set_weekly_report_schedule(
        db_session,
        team,
        weekday=slot.weekday(),
        hour=slot.hour,
        send_empty=True,
        user_id=_member(db_session, team).id,
    )

    tasks.send_due_weekly_reports()
    tasks.send_due_weekly_reports()  # the next tick, or a redelivery

    start, _ = service.weekly_period(slot)
    row = db_session.get(IntelReport, (team, start))
    assert row is not None and row.posted_at is not None
    slack.return_value.post_message.assert_called_once()  # type: ignore[attr-defined]


@pytest.mark.usefixtures("use_test_session")
def test_a_post_that_failed_is_tried_again_and_the_report_not_rewritten(
    db_session: Session, team: str, slack: object
) -> None:
    _active(db_session, team)
    now = datetime.now(UTC)
    slot = service.latest_weekly_slot(weekday=0, hour=9, now=now)
    service.set_weekly_report_schedule(
        db_session,
        team,
        weekday=slot.weekday(),
        hour=slot.hour,
        send_empty=True,
        user_id=_member(db_session, team).id,
    )
    post = slack.return_value.post_message  # type: ignore[attr-defined]
    post.side_effect = [RuntimeError("slack down"), None]

    tasks.send_due_weekly_reports()
    start, _ = service.weekly_period(slot)
    first = db_session.get(IntelReport, (team, start))
    assert first is not None and first.posted_at is None
    written = first.body_markdown

    tasks.send_due_weekly_reports()

    db_session.refresh(first)
    assert first.posted_at is not None and first.body_markdown == written
    assert post.call_count == 2


@pytest.mark.usefixtures("use_test_session")
def test_a_week_with_nothing_to_say_is_kept_but_not_posted_unless_the_team_asks(
    db_session: Session, team: str, slack: object
) -> None:
    """No meeting analysed, nothing overdue, nothing carried: the row is kept,
    marked so the next tick does not try again, and nothing reaches Slack."""
    tasks.generate_weekly_report(team, period_end="2026-09-14")
    tasks.generate_weekly_report(team, period_end="2026-09-14")

    row = db_session.get(IntelReport, (team, date(2026, 9, 7)))
    assert row is not None and row.posted_at is None and row.not_posted == "empty"
    slack.return_value.post_message.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.usefixtures("use_test_session")
def test_asking_again_for_a_posted_week_does_not_post_it_twice(
    db_session: Session, team: str, slack: object
) -> None:
    service.set_weekly_report_schedule(
        db_session, team, weekday=0, hour=9, send_empty=True, user_id=_member(db_session, team).id
    )
    tasks.generate_weekly_report(team, period_end="2026-09-14")
    tasks.generate_weekly_report(team, period_end="2026-09-14")

    slack.return_value.post_message.assert_called_once()  # type: ignore[attr-defined]


def test_the_hourly_task_is_declared_periodic() -> None:
    from autune_core.periodic import schedule_of

    assert schedule_of(tasks.send_due_weekly_reports) == timedelta(hours=1)


def test_a_week_is_empty_only_without_meetings_overdue_or_carried_items() -> None:
    empty = {"meeting_count": 0, "overdue_action_items": None, "carried_over_action_items": 0}

    assert service.weekly_report_is_empty(empty)
    assert not service.weekly_report_is_empty({**empty, "meeting_count": 1})
    assert not service.weekly_report_is_empty({**empty, "overdue_action_items": 2})
    assert not service.weekly_report_is_empty({**empty, "carried_over_action_items": 1})


# --- the route ---------------------------------------------------------------------


def _client(db_session: Session, user: User | None):  # type: ignore[no-untyped-def]
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from autune_core import AutuneError, get_session
    from autune_core.auth import current_user
    from autune_intelligence.router import router

    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/intelligence")
    app.dependency_overrides[get_session] = lambda: db_session
    if user is not None:
        app.dependency_overrides[current_user] = lambda: user
    return TestClient(app)


def test_a_member_reads_and_changes_the_schedule(db_session: Session, team: str) -> None:
    client = _client(db_session, _member(db_session, team))
    url = f"/api/intelligence/weekly-report-schedule/{team}"

    assert client.get(url).json()["weekday"] == 0
    changed = client.put(url, json={"weekday": 4, "hour": 17, "send_empty": True}).json()

    assert (changed["weekday"], changed["hour"], changed["send_empty"]) == (4, 17, True)
    assert changed["updated_by_name"] == "정하는 사람"
    assert client.put(url, json={"weekday": 9, "hour": 17, "send_empty": True}).status_code == 422


def test_the_schedule_is_for_the_teams_members(db_session: Session, team: str) -> None:
    url = f"/api/intelligence/weekly-report-schedule/{team}"
    outsider = User(email=f"out-{uuid.uuid4().hex}@example.com", display_name="밖")
    db_session.add(outsider)
    db_session.flush()

    for user in (None, outsider):
        client = _client(db_session, user)
        assert client.get(url).status_code == 403
        body = {"weekday": 1, "hour": 9, "send_empty": False}
        assert client.put(url, json=body).status_code == 403
