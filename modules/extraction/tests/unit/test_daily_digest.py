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
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Meeting, Team, TeamMember, User, get_session
from autune_core.errors import AutuneError, ValidationError
from autune_extraction import reminders, service, tasks
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
    edited(session, "act_doing", "status", at=MONDAY_NOON_KST)  # moved to 진행 중: not "new"
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


@pytest.fixture
def api(session: Session) -> TestClient:
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
    assert api.get(f"{PREFIX}/me/notification-pause").json() == {
        "starts_on": None,
        "ends_on": None,
    }

    answer = api.put(
        f"{PREFIX}/me/notification-pause",
        json={"starts_on": first.isoformat(), "ends_on": last.isoformat()},
    )

    assert answer.status_code == 200
    assert answer.json() == {"starts_on": first.isoformat(), "ends_on": last.isoformat()}
    assert api.get(f"{PREFIX}/me/notification-pause").json() == answer.json()
    (row,) = session.query(ExtNotificationPause).all()
    assert row.user_id == READER, "the signed-in person's, and nobody else's"

    cleared = api.put(f"{PREFIX}/me/notification-pause", json={"starts_on": None, "ends_on": None})
    assert cleared.json() == {"starts_on": None, "ends_on": None}
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
