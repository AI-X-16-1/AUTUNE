"""The morning DM, and a person's own leave dates (the user, 2026-10-05).

The rules under test: Tuesday to Friday mornings in Korea only -- Monday is
the weekly digest's, a weekend has nothing; one per person and team, once a
day; "what changed" is read from the edit events since the last one and says
only what they can support; today's work puts the late first and each item
once; nobody who turned their reminders off or paused the day gets one, and
that is read again at send time; a pause stops Monday's digest too, belongs to
the person alone, and is not kept past its last day.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_contracts.enums import ActionStatus
from autune_core import (
    Base,
    Meeting,
    PrivacyViolationError,
    Team,
    TeamMember,
    User,
    get_session,
)
from autune_core.errors import AutuneError, ValidationError
from autune_extraction import leave_calendar, reminders, service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtDailyDigest,
    ExtEditEvent,
    ExtNotificationPause,
    ExtWeeklyDigest,
)
from autune_extraction.reminders import DailyDigest, DigestLine, build_daily_digest, daily_day
from autune_extraction.router import router
from autune_extraction.schemas import ActionItemUpdate
from autune_integrations.errors import TransientIntegrationError
from autune_integrations.fakes import FakeSlack as CheckedSlack

from .conftest import READER, sign_in

PREFIX = "/api/extraction"
TUESDAY = date(2026, 10, 6)
TUESDAY_10_KST = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)
MONDAY_10_KST = datetime(2026, 10, 5, 1, 0, tzinfo=UTC)
MONDAY_NOON_KST = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)  # after Monday 00:00 KST


# --- when ------------------------------------------------------------------------


def test_a_morning_dm_goes_tuesday_to_friday_mornings_in_korea() -> None:
    assert daily_day(TUESDAY_10_KST) == TUESDAY
    assert daily_day(datetime(2026, 10, 9, 0, 0, tzinfo=UTC)) == date(2026, 10, 9)  # Fri 09:00
    assert daily_day(MONDAY_10_KST) is None, "Monday is the weekly digest's"
    assert daily_day(datetime(2026, 10, 10, 1, 0, tzinfo=UTC)) is None  # Saturday
    assert daily_day(datetime(2026, 10, 11, 1, 0, tzinfo=UTC)) is None  # Sunday
    assert daily_day(datetime(2026, 10, 5, 23, 59, tzinfo=UTC)) is None  # Tue 08:59 KST
    assert daily_day(datetime(2026, 10, 6, 3, 0, tzinfo=UTC)) is None  # Tue 12:00 KST


def test_a_monday_never_gets_both() -> None:
    """The two windows share no moment: the weekly digest's day is no morning
    DM's, at any hour."""
    for hour in range(24):
        moment = datetime(2026, 10, 5, hour, 30, tzinfo=UTC)
        if reminders.digest_week(moment) is not None:
            assert daily_day(moment) is None


# --- what it says ----------------------------------------------------------------


def test_the_message_says_what_changed_then_today_late_first_and_escapes() -> None:
    text = build_daily_digest(
        DailyDigest(
            done=[DigestLine("로그인 고치기", None, "주간 회의")],
            taken_on=[DigestLine("새 일 <!channel>", date(2026, 10, 8), None)],
            late=[DigestLine("늦은 일", date(2026, 10, 1), "기획 <회의>")],
            due_today=[DigestLine("오늘 일", TUESDAY, None)],
            in_progress=[DigestLine("하던 일", None, None)],
            others=3,
        ),
        board_url="https://autune.example/actions",
    )

    assert text.split("\n") == [
        "좋은 아침입니다. 지난 진행 상황과 오늘 할 일입니다.",
        "지난 진행 상황",
        "• 완료: 로그인 고치기 · 주간 회의",
        "• 새로 맡음: 새 일 &lt;!channel&gt;",
        "오늘 할 일",
        "• 기한 지남(2026-10-01): 늦은 일 · 기획 &lt;회의&gt;",
        "• 오늘 기한: 오늘 일",
        "• 진행 중: 하던 일",
        "그 밖의 열린 액션 아이템 3개",
        "https://autune.example/actions",
    ]


def test_a_quiet_day_says_so_rather_than_leaving_a_heading_empty() -> None:
    text = build_daily_digest(DailyDigest(others=2), board_url="https://autune.example/actions")

    assert "• 바뀐 것이 없습니다." in text
    assert "• 오늘 기한이거나 진행 중인 항목이 없습니다." in text
    assert "그 밖의 열린 액션 아이템 2개" in text


def test_a_long_list_is_cut_and_counted() -> None:
    late = [DigestLine(f"늦은 일 {n}", date(2026, 10, 1), None) for n in range(8)]

    lines = build_daily_digest(DailyDigest(late=late), board_url="u").split("\n")

    assert sum(1 for line in lines if line.startswith("• 기한 지남(")) == 5
    assert "• 기한 지남 외 3개" in lines


def test_nothing_changed_and_nothing_open_is_no_message() -> None:
    assert DailyDigest().empty is True
    assert DailyDigest(others=1).empty is False
    assert DailyDigest(done=[DigestLine("끝", None, None)]).empty is False


# --- who is owed, and what is read -------------------------------------------------


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
            s.add(Meeting(id=f"mtg_{team}", team_id=team, title=f"{team} 회의"))
        s.add(TeamMember(team_id="team_1", user_id="user_kim"))
        s.add(TeamMember(team_id="team_2", user_id="user_kim"))
        s.add(TeamMember(team_id="team_1", user_id="user_lee"))
        rows = [
            ("act_late", "mtg_team_1", "user_kim", "todo", "늦은 일", date(2026, 10, 2)),
            ("act_today", "mtg_team_1", "user_kim", "todo", "오늘 일", TUESDAY),
            ("act_doing", "mtg_team_1", "user_kim", "in_progress", "하던 일", None),
            ("act_later", "mtg_team_1", "user_kim", "todo", "나중 일", date(2026, 10, 20)),
            ("act_done", "mtg_team_1", "user_kim", "done", "어제 끝낸 일", None),
            ("act_old", "mtg_team_1", "user_kim", "done", "오래전에 끝낸 일", None),
            ("act_other", "mtg_team_2", "user_kim", "todo", "다른 팀 일", None),
            ("act_other_done", "mtg_team_2", "user_kim", "done", "다른 팀에서 끝낸 일", None),
            ("act_lee", "mtg_team_1", "user_lee", "todo", "이 님의 일", TUESDAY),
            ("act_lee_done", "mtg_team_1", "user_lee", "done", "이 님이 끝낸 일", None),
            ("act_gone", "mtg_team_1", "user_gone", "todo", "팀에 없는 사람 일", TUESDAY),
            ("act_draft", "mtg_team_1", "user_lee", "needs_confirmation", "확인 전 일", None),
        ]
        for item_id, meeting, who, status, text, due in rows:
            s.add(
                ExtActionItem(
                    id=item_id,
                    meeting_id=meeting,
                    description=text,
                    assignee_id=who,
                    status=status,
                    due_date=due,
                    confidence=0.9,
                    origin="model",
                )
            )
        s.flush()
        yield s


def edited(
    session: Session, item_id: str, *fields: str, at: datetime, kind: str = "edited"
) -> None:
    item = session.get(ExtActionItem, item_id)
    assert item is not None
    session.add(
        ExtEditEvent(
            meeting_id=item.meeting_id,
            action_item_id=item_id,
            kind=kind,
            fields=",".join(sorted(fields)) or None,
            created_at=at,
        )
    )
    session.flush()


def owed_for(session: Session, user: str = "user_kim", team: str = "team_1") -> object:
    (found,) = [
        d
        for d in service.daily_digests_to_send(session, now=TUESDAY_10_KST)
        if (d.user_id, d.team_id) == (user, team)
    ]
    return found


def test_one_is_owed_per_person_and_team_with_open_work(session: Session) -> None:
    owed = service.daily_digests_to_send(session, now=TUESDAY_10_KST)

    assert [(d.user_id, d.team_id, d.day) for d in owed] == [
        ("user_kim", "team_1", TUESDAY),
        ("user_kim", "team_2", TUESDAY),
        ("user_lee", "team_1", TUESDAY),
    ]
    assert service.daily_digests_to_send(session, now=MONDAY_10_KST) == []


def test_what_changed_is_read_from_the_edits_since_the_last_one(session: Session) -> None:
    edited(session, "act_done", "status", at=MONDAY_NOON_KST)
    edited(session, "act_old", "status", at=datetime(2026, 9, 1, tzinfo=UTC))  # long ago
    edited(session, "act_today", "assignee_id", at=MONDAY_NOON_KST)
    edited(session, "act_later", kind="created", at=MONDAY_NOON_KST)
    # Confirmed long ago, moved to 진행 중 yesterday: not "new".
    edited(session, "act_doing", "status", at=datetime(2026, 9, 1, tzinfo=UTC))
    edited(session, "act_doing", "status", at=MONDAY_NOON_KST)
    edited(session, "act_lee", "status", at=MONDAY_NOON_KST)  # somebody else's
    edited(session, "act_lee_done", "status", at=MONDAY_NOON_KST)  # and finished by them
    edited(session, "act_other_done", "status", at=MONDAY_NOON_KST)  # theirs, another team's

    content = service.daily_digest_content(
        session,
        owed_for(session),  # type: ignore[arg-type]
        since=reminders.previous_morning(TUESDAY).astimezone(UTC),
        now=TUESDAY_10_KST,
    )

    assert [line.description for line in content.done] == ["어제 끝낸 일"]
    assert [line.description for line in content.taken_on] == ["오늘 일", "나중 일"]
    assert [line.description for line in content.late] == ["늦은 일"]
    assert [line.description for line in content.due_today] == ["오늘 일"]
    assert [line.description for line in content.in_progress] == ["하던 일"]
    assert content.others == 1, "the item due later: counted, not listed"


def content_for(session: Session) -> reminders.DailyDigest:
    return service.daily_digest_content(
        session,
        owed_for(session),  # type: ignore[arg-type]
        since=reminders.previous_morning(TUESDAY).astimezone(UTC),
        now=TUESDAY_10_KST,
    )


def drafted(session: Session, item_id: str, *, origin: str = "model") -> ExtActionItem:
    """One more item of user_kim's on team_1, as the pipeline or the chat
    drafts it: ``needs_confirmation``, and no ``created`` event."""
    item = ExtActionItem(
        id=item_id,
        meeting_id="mtg_team_1",
        description=f"{item_id} 초안",
        assignee_id="user_kim",
        status="needs_confirmation",
        confidence=0.9,
        origin=origin,
    )
    session.add(item)
    session.flush()
    return item


def moved(session: Session, item: ExtActionItem, status: ActionStatus, *, at: datetime) -> None:
    """A status change through the real ``update_action_item`` -- so the edit
    event is whatever that function records -- stamped ``at``."""
    before = {e.id for e in session.query(ExtEditEvent)}
    service.update_action_item(session, item, ActionItemUpdate(status=status))
    session.flush()
    (event,) = [e for e in session.query(ExtEditEvent) if e.id not in before]
    event.created_at = at
    session.flush()


@pytest.mark.parametrize("origin", ["model", "chat"])
def test_an_item_confirmed_since_the_last_one_is_newly_held(session: Session, origin: str) -> None:
    """Review of #833: the usual way a person comes to hold an item. The draft
    has no ``created`` event and confirming it records only a status edit --
    read from fixtures that wrote ``created`` by hand, yesterday's confirmed
    items came out as "nothing changed"."""
    item = drafted(session, "act_new", origin=origin)
    assert "act_new 초안" not in [line.description for line in content_for(session).taken_on]

    moved(session, item, ActionStatus.TODO, at=MONDAY_NOON_KST)

    assert [line.description for line in content_for(session).taken_on] == ["act_new 초안"]


def test_an_item_confirmed_straight_into_progress_is_newly_held_too(session: Session) -> None:
    item = drafted(session, "act_new")

    moved(session, item, ActionStatus.IN_PROGRESS, at=MONDAY_NOON_KST)

    content = content_for(session)
    assert [line.description for line in content.taken_on] == ["act_new 초안"]
    assert "act_new 초안" in [line.description for line in content.in_progress]


def test_an_item_confirmed_before_and_only_moved_since_is_not_new(session: Session) -> None:
    item = drafted(session, "act_old_draft")
    moved(session, item, ActionStatus.TODO, at=datetime(2026, 9, 20, tzinfo=UTC))

    moved(session, item, ActionStatus.IN_PROGRESS, at=MONDAY_NOON_KST)

    assert content_for(session).taken_on == []


def test_a_persons_own_item_moved_since_is_not_new(session: Session) -> None:
    """Made by a person, so born past ``needs_confirmation``: its first status
    edit is a move, not a confirmation."""
    item = drafted(session, "act_mine", origin="user")
    item.status = "todo"
    session.flush()

    moved(session, item, ActionStatus.IN_PROGRESS, at=MONDAY_NOON_KST)

    assert content_for(session).taken_on == []


def test_an_edit_from_before_fields_were_named_may_have_been_the_confirmation(
    session: Session,
) -> None:
    item = drafted(session, "act_legacy")
    session.add(
        ExtEditEvent(
            meeting_id="mtg_team_1",
            action_item_id="act_legacy",
            kind="edited",
            fields=None,
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    item.status = "todo"
    session.flush()

    moved(session, item, ActionStatus.IN_PROGRESS, at=MONDAY_NOON_KST)

    assert content_for(session).taken_on == []


def test_a_meeting_past_its_retention_is_in_neither_half(session: Session) -> None:
    """What the weekly digest leaves out, this leaves out -- finished items too."""
    owed = owed_for(session)
    edited(session, "act_done", "status", at=MONDAY_NOON_KST)
    meeting = session.get(Meeting, "mtg_team_1")
    assert meeting is not None
    meeting.expires_at = TUESDAY_10_KST - timedelta(hours=1)
    session.flush()

    content = service.daily_digest_content(
        session,
        owed,  # type: ignore[arg-type]
        since=reminders.previous_morning(TUESDAY).astimezone(UTC),
        now=TUESDAY_10_KST,
    )

    assert content.empty


def test_a_dm_goes_to_its_person_once_a_day_with_their_own_items_only(
    session: Session,
) -> None:
    edited(session, "act_done", "status", at=MONDAY_NOON_KST)
    slack = FakeSlack()
    owed = owed_for(session)

    assert service.send_daily_digest(session, slack, owed, now=TUESDAY_10_KST) is True  # type: ignore[arg-type]
    assert service.send_daily_digest(session, slack, owed, now=TUESDAY_10_KST) is False  # type: ignore[arg-type]

    ((who, text),) = slack.sent
    assert who == "user_kim"
    assert "• 완료: 어제 끝낸 일 · team_1 회의" in text
    assert "• 기한 지남(2026-10-02): 늦은 일" in text
    assert "다른 팀 일" not in text, "one team's bot, its own work"
    assert "이 님의 일" not in text and "팀에 없는 사람 일" not in text
    assert [
        (d.user_id, d.team_id) for d in service.daily_digests_to_send(session, now=TUESDAY_10_KST)
    ] == [("user_kim", "team_2"), ("user_lee", "team_1")]


def test_the_next_one_counts_from_when_the_last_one_went(session: Session) -> None:
    """Friday's DM went; the weekend's and Monday's changes are Tuesday's news."""
    friday = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
    session.add(
        ExtDailyDigest(user_id="user_kim", team_id="team_1", day=date(2026, 10, 2), sent_at=friday)
    )
    edited(session, "act_done", "status", at=friday + timedelta(hours=5))  # Friday afternoon
    edited(session, "act_old", "status", at=friday - timedelta(hours=5))  # before that DM
    slack = FakeSlack()

    service.send_daily_digest(session, slack, owed_for(session), now=TUESDAY_10_KST)  # type: ignore[arg-type]

    text = slack.sent[0][1]
    assert "어제 끝낸 일" in text and "오래전에 끝낸 일" not in text


def test_someone_long_away_gets_a_week_of_changes_not_a_history(session: Session) -> None:
    long_ago = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
    session.add(
        ExtDailyDigest(user_id="user_kim", team_id="team_1", day=date(2026, 8, 1), sent_at=long_ago)
    )
    edited(session, "act_old", "status", at=datetime(2026, 9, 1, tzinfo=UTC))
    edited(session, "act_done", "status", at=MONDAY_NOON_KST)
    slack = FakeSlack()

    service.send_daily_digest(session, slack, owed_for(session), now=TUESDAY_10_KST)  # type: ignore[arg-type]

    text = slack.sent[0][1]
    assert "어제 끝낸 일" in text and "오래전에 끝낸 일" not in text


def test_someone_who_turned_reminders_off_is_owed_none_and_sent_none(session: Session) -> None:
    owed = owed_for(session)
    service.set_due_reminders(session, "user_kim", on=False, now=TUESDAY_10_KST)
    slack = FakeSlack()

    assert [d.user_id for d in service.daily_digests_to_send(session, now=TUESDAY_10_KST)] == [
        "user_lee"
    ]
    assert service.send_daily_digest(session, slack, owed, now=TUESDAY_10_KST) is False  # type: ignore[arg-type]
    assert slack.sent == [] and session.query(ExtDailyDigest).count() == 0


# --- a person's own leave dates ------------------------------------------------------


def pause(session: Session, user: str, first: date, last: date) -> None:
    service.set_notification_pause(session, user, starts_on=first, ends_on=last, now=MONDAY_10_KST)


def test_a_paused_day_gets_no_morning_dm_and_the_day_after_does(session: Session) -> None:
    pause(session, "user_kim", TUESDAY, TUESDAY)

    assert [d.user_id for d in service.daily_digests_to_send(session, now=TUESDAY_10_KST)] == [
        "user_lee"
    ]
    wednesday = TUESDAY_10_KST + timedelta(days=1)
    assert "user_kim" in [d.user_id for d in service.daily_digests_to_send(session, now=wednesday)]


def test_going_on_leave_after_the_list_was_made_sends_nothing(session: Session) -> None:
    owed = owed_for(session)
    pause(session, "user_kim", TUESDAY, date(2026, 10, 9))
    slack = FakeSlack()

    assert service.send_daily_digest(session, slack, owed, now=TUESDAY_10_KST) is False  # type: ignore[arg-type]
    assert slack.sent == [] and session.query(ExtDailyDigest).count() == 0


def test_a_pause_stops_mondays_digest_too(session: Session) -> None:
    """The user's answer: leave blocks the morning DM and the Monday digest."""
    (first, *_rest) = service.weekly_digests_to_send(session, now=MONDAY_10_KST)
    assert first.user_id == "user_kim"
    pause(session, "user_kim", date(2026, 10, 5), date(2026, 10, 7))
    slack = FakeSlack()

    assert "user_kim" not in [
        d.user_id for d in service.weekly_digests_to_send(session, now=MONDAY_10_KST)
    ]
    assert service.send_weekly_digest(session, slack, first, now=MONDAY_10_KST) is False
    assert slack.sent == [] and session.query(ExtWeeklyDigest).count() == 0


def test_a_pause_is_one_range_replaced_or_cleared(session: Session) -> None:
    pause(session, "user_kim", TUESDAY, TUESDAY)
    pause(session, "user_kim", date(2026, 10, 12), date(2026, 10, 16))

    (row,) = session.query(ExtNotificationPause).all()
    assert (row.starts_on, row.ends_on) == (date(2026, 10, 12), date(2026, 10, 16))
    assert service.notifications_paused(session, "user_kim", TUESDAY) is False
    assert service.notifications_paused(session, "user_kim", date(2026, 10, 14)) is True

    service.set_notification_pause(
        session, "user_kim", starts_on=None, ends_on=None, now=MONDAY_10_KST
    )
    assert session.query(ExtNotificationPause).count() == 0


@pytest.mark.parametrize(
    ("first", "last"),
    [
        pytest.param(date(2026, 10, 9), date(2026, 10, 6), id="ends-before-it-starts"),
        pytest.param(date(2026, 10, 6), None, id="half-a-range"),
        pytest.param(date(2026, 10, 6), date(2027, 10, 6), id="a-year"),
        pytest.param(date(2026, 9, 1), date(2026, 9, 3), id="already-over"),
        pytest.param(date(2099, 1, 1), date(2099, 1, 5), id="decades-ahead"),
    ],
)
def test_a_pause_that_makes_no_sense_is_refused_and_changes_nothing(
    session: Session, first: date, last: date | None
) -> None:
    with pytest.raises(ValidationError):
        service.set_notification_pause(
            session, "user_kim", starts_on=first, ends_on=last, now=MONDAY_10_KST
        )


def test_a_pause_is_not_kept_past_its_last_day(session: Session) -> None:
    """When a person was away is kept only while it stops a message."""
    pause(session, "user_kim", date(2026, 10, 5), TUESDAY)
    pause(session, "user_lee", TUESDAY, date(2026, 10, 9))

    assert service.forget_ended_pauses(session, today=TUESDAY) == 0, "its last day is today"
    assert service.forget_ended_pauses(session, today=date(2026, 10, 7)) == 1

    assert [p.user_id for p in session.query(ExtNotificationPause)] == ["user_lee"]


# --- the task and the routes -----------------------------------------------------------


def test_the_setting_is_off_by_default_and_the_task_then_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ExtractionSettings(_env_file=None).daily_digest is False  # type: ignore[call-arg]
    monkeypatch.setattr(tasks, "get_settings", lambda: SimpleNamespace(daily_digest=False))
    monkeypatch.setattr(tasks, "session_scope", None)  # would fail if it were opened

    assert tasks.send_daily_digests() == []


@dataclass
class _Config:
    secret: str

    def require_secret(self) -> str:
        return self.secret


class _Clock(datetime):
    """``datetime.now`` as the tasks call it, held at a moment a test picks."""

    moment = TUESDAY_10_KST

    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def, override]
        return cls.moment


@pytest.fixture
def checked_slack(session: Session, monkeypatch: pytest.MonkeyPatch) -> CheckedSlack:
    """Both digest tasks with this session, connected teams, both switches on,
    Tuesday 10:00 in Korea, and the fake that runs the outbound check -- the
    one that refuses a phone number, as the real client does."""

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
        lambda: ExtractionSettings(_env_file=None, daily_digest=True, weekly_digest=True),  # type: ignore[call-arg]
    )
    _Clock.moment = TUESDAY_10_KST
    return fake


def refusable(session: Session) -> None:
    """One of user_kim's team_1 items now carries a phone number somebody typed."""
    item = session.get(ExtActionItem, "act_today")
    assert item is not None
    item.description = "010-1234-5678로 전화하기"
    session.flush()


def test_a_refused_morning_dm_is_raised_after_the_others_go_and_not_tried_again(
    session: Session, checked_slack: CheckedSlack
) -> None:
    """The outbound check refuses one person's DM. The others still get theirs,
    the refusal is raised, not swallowed -- and the day's claim is kept, so the
    next run, ten minutes later, does not refuse the same text again."""
    refusable(session)

    with pytest.raises(PrivacyViolationError, match="user_kim"):
        tasks.send_daily_digests()

    assert sorted(m.channel for m in checked_slack.sent) == ["user_kim", "user_lee"]
    assert not any("010-1234-5678" in m.text for m in checked_slack.sent)
    assert sorted((d.user_id, d.team_id) for d in session.query(ExtDailyDigest)) == [
        ("user_kim", "team_1"),  # refused, settled for the day
        ("user_kim", "team_2"),
        ("user_lee", "team_1"),
    ]

    assert tasks.send_daily_digests() == [], "nothing owed, nothing refused, nothing raised"
    assert len(checked_slack.sent) == 2


def test_a_morning_dm_slack_did_not_take_stays_owed(
    session: Session, checked_slack: CheckedSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a refusal is settled. A send that failed takes its claim back and
    is tried again next run."""

    def down(user_id: str, text: str, blocks=None) -> str:  # type: ignore[no-untyped-def]
        raise TransientIntegrationError("slack timed out")

    monkeypatch.setattr(checked_slack, "send_dm", down)

    assert tasks.send_daily_digests() == []

    assert session.query(ExtDailyDigest).count() == 0
    assert len(service.daily_digests_to_send(session, now=TUESDAY_10_KST)) == 3


def test_a_refused_morning_dm_whose_claim_cannot_be_kept_is_still_raised(
    session: Session, checked_slack: CheckedSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    refusable(session)

    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("database gone")

    monkeypatch.setattr(service, "settle_refused_daily_digest", broken)

    with capture_logs() as logs, pytest.raises(PrivacyViolationError, match="user_kim"):
        tasks.send_daily_digests()

    assert "extraction_daily_digest_refusal_not_kept" in [e["event"] for e in logs]
    assert "database gone" not in repr(logs) and "010-1234-5678" not in repr(logs)


def test_a_refused_monday_digest_is_raised_once_and_not_tried_again(
    session: Session, checked_slack: CheckedSlack
) -> None:
    """The same hole in the weekly digest (#792): its refused claim rolled back,
    so the same text was refused every ten minutes until eight that night."""
    refusable(session)
    _Clock.moment = MONDAY_10_KST

    with pytest.raises(PrivacyViolationError, match="user_kim"):
        tasks.send_weekly_digests()

    assert sorted(m.channel for m in checked_slack.sent) == ["user_kim", "user_lee"]
    assert session.query(ExtWeeklyDigest).count() == 3

    assert tasks.send_weekly_digests() == []
    assert len(checked_slack.sent) == 2


@pytest.fixture
def api(session: Session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # The pause read says whether the caller has a calendar connected; here
    # nobody has (``user_integrations`` is JSONB and not in this SQLite).
    monkeypatch.setattr(leave_calendar, "load_user_integration", lambda *_: None)
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session, team_id="team_1")
    return TestClient(app)


def test_a_person_sets_reads_and_clears_their_own_pause(api: TestClient, session: Session) -> None:
    today = reminders.korean_day(datetime.now(tz=UTC))
    first, last = today + timedelta(days=1), today + timedelta(days=3)
    nothing_on_a_calendar = {
        "on_calendar": False,
        "calendar_leave": False,
        "calendar_connected": False,
    }
    assert api.get(f"{PREFIX}/me/notification-pause").json() == {
        "starts_on": None,
        "ends_on": None,
        **nothing_on_a_calendar,
        "calendar": None,
    }

    answer = api.put(
        f"{PREFIX}/me/notification-pause",
        json={"starts_on": first.isoformat(), "ends_on": last.isoformat()},
    )

    assert answer.status_code == 200
    assert answer.json() == {
        "starts_on": first.isoformat(),
        "ends_on": last.isoformat(),
        **nothing_on_a_calendar,
        "calendar": "off",
    }
    assert api.get(f"{PREFIX}/me/notification-pause").json() == {**answer.json(), "calendar": None}
    (row,) = session.query(ExtNotificationPause).all()
    assert row.user_id == READER, "the signed-in person's, and nobody else's"

    cleared = api.put(f"{PREFIX}/me/notification-pause", json={"starts_on": None, "ends_on": None})
    assert cleared.json() == {
        "starts_on": None,
        "ends_on": None,
        **nothing_on_a_calendar,
        "calendar": "off",
    }
    assert session.query(ExtNotificationPause).count() == 0


def test_the_route_names_nobody_and_refuses_a_backwards_range(
    api: TestClient, session: Session
) -> None:
    today = reminders.korean_day(datetime.now(tz=UTC))
    body = {"starts_on": (today + timedelta(days=3)).isoformat(), "ends_on": today.isoformat()}

    assert api.put(f"{PREFIX}/me/notification-pause", json=body).status_code == 422
    assert (
        api.put(
            f"{PREFIX}/me/notification-pause",
            json={"user_id": "user_kim", "starts_on": None, "ends_on": None},
        ).status_code
        == 422
    ), "a body naming a person is refused, not ignored"
    assert session.query(ExtNotificationPause).count() == 0


def test_the_screen_is_told_when_this_server_reads_leave_from_a_calendar(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "autune_extraction.router.get_settings",
        lambda: ExtractionSettings(_env_file=None, leave_from_calendar=True),  # type: ignore[call-arg]
    )

    assert api.get(f"{PREFIX}/me/notification-pause").json()["calendar_leave"] is True
    assert (
        api.put(
            f"{PREFIX}/me/notification-pause",
            json={"starts_on": None, "ends_on": None, "calendar_leave": False},
        ).status_code
        == 422
    ), "it is the deployment's, not something a person sends"
