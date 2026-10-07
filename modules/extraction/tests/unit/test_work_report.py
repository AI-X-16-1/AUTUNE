"""The "오늘 업무 보고" draft (the user, 2026-10-07).

SQLite in memory. What is under test: when one goes, what its four parts say
and cannot say, who is owed one, that it goes once and to its person only, and
what the task does with a refusal.

**"Today" is made by the board's own writer.** A status is changed with
``service.update_action_item``, which is what records the edit this feature
reads -- so the tests run on today's real date, and the hour is fixed by
replacing ``report_day`` where the window is not what is being tested. The one
row written by hand is an edit from before today, which no writer can make.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_contracts.enums import ActionStatus
from autune_core import Base, Meeting, PrivacyViolationError, Team, TeamMember, User
from autune_extraction import reminders, router, service, tasks, work_report
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem, ExtEditEvent, ExtWorkReport
from autune_extraction.reminders import DigestLine
from autune_extraction.schemas import ActionItemUpdate
from autune_extraction.work_report import (
    WorkReport,
    WorkReportOwed,
    build_text,
    report_day,
)
from autune_integrations.errors import SlackRecipientNotLinkedError, TransientIntegrationError
from autune_integrations.fakes import FakeSlack as CheckedSlack

WEDNESDAY = date(2026, 10, 7)
BOARD = "https://autune.example/actions"


def now() -> datetime:
    return datetime.now(tz=UTC)


def today() -> date:
    return reminders.korean_day(now())


# --- when ------------------------------------------------------------------------


def test_a_report_goes_in_the_last_hour_of_a_working_afternoon_in_korea() -> None:
    def at(day: int, hour: int, minute: int = 0) -> date | None:
        # October 2026: the 5th is a Monday. Hours are Korea time.
        return report_day(datetime(2026, 10, day, hour, minute, tzinfo=reminders.KST))

    assert at(7, 16) == WEDNESDAY
    assert at(7, 16, 59) == WEDNESDAY
    assert at(5, 16, 30) == date(2026, 10, 5), "Monday too: no weekly report to leave it to"
    assert at(9, 16, 30) == date(2026, 10, 9)  # Friday
    assert at(7, 15, 59) is None
    assert at(7, 17) is None, "nothing after 17:00 (the user, 2026-10-07)"
    assert at(7, 18) is None and at(7, 20) is None
    assert at(7, 9) is None, "and not the next morning: the morning is the morning DM's"
    assert at(10, 16, 30) is None  # Saturday
    assert at(11, 16, 30) is None  # Sunday


def test_the_day_is_koreas_not_utcs() -> None:
    """16:30 on Wednesday in Korea is 07:30 UTC."""
    assert report_day(datetime(2026, 10, 7, 7, 30, tzinfo=UTC)) == WEDNESDAY
    assert report_day(datetime(2026, 10, 7, 16, 30, tzinfo=UTC)) is None  # 01:30 Thursday
    assert work_report.day_start(WEDNESDAY) == datetime(2026, 10, 6, 15, 0, tzinfo=UTC)


# --- what it says ----------------------------------------------------------------


def test_the_text_is_a_line_to_the_person_then_the_draft_headed_by_its_team() -> None:
    text = build_text(
        WorkReport(
            day=WEDNESDAY,
            team_name="플랫폼팀",
            done=[DigestLine("로그인 고치기", None, "주간 회의")],
            moved=[DigestLine("배포 점검표 <!channel>", date(2026, 10, 9), None)],
            carried=[
                DigestLine("하던 일", None, "기획 <회의>"),
                DigestLine("오늘까지인 일", WEDNESDAY, None),
            ],
            late=[DigestLine("늦은 일", date(2026, 10, 1), None)],
            others=3,
        ),
        board_url=BOARD,
    )

    assert text.split("\n") == [
        "오늘 업무 보고 초안입니다. 고쳐서 팀에 붙여 넣으셔도 됩니다.",
        "플랫폼팀 업무 보고 (2026-10-07)",
        "끝낸 일",
        "• 로그인 고치기 · 주간 회의",
        "진행한 일",
        "• 배포 점검표 &lt;!channel&gt;",
        "내일로 넘어가는 일",
        "• 하던 일 · 기획 &lt;회의&gt;",
        "• 오늘까지인 일 (오늘 기한)",
        "늦은 일",
        "• 늦은 일 (기한 2026-10-01 지남)",
        "그 밖의 열린 액션 아이템 3개",
        BOARD,
    ]


def test_a_part_with_nothing_in_it_is_left_out_and_a_team_name_is_escaped() -> None:
    text = build_text(
        WorkReport(day=WEDNESDAY, team_name="<팀>", done=[DigestLine("끝낸 일", None, None)]),
        board_url=BOARD,
    )

    assert text.split("\n") == [
        "오늘 업무 보고 초안입니다. 고쳐서 팀에 붙여 넣으셔도 됩니다.",
        "&lt;팀&gt; 업무 보고 (2026-10-07)",
        "끝낸 일",
        "• 끝낸 일",
        BOARD,
    ]


def test_a_long_list_is_cut_and_counted() -> None:
    done = [DigestLine(f"끝낸 일 {n}", None, None) for n in range(8)]

    lines = build_text(WorkReport(day=WEDNESDAY, done=done), board_url=BOARD).split("\n")

    assert sum(1 for line in lines if line.startswith("• 끝낸 일 ")) == 5
    assert "• 외 3개" in lines


def test_only_a_day_with_something_finished_or_moved_is_a_day_to_report() -> None:
    line = [DigestLine("일", None, None)]
    assert WorkReport(day=WEDNESDAY).empty is True
    assert WorkReport(day=WEDNESDAY, carried=line, late=line, others=4).empty is True
    assert WorkReport(day=WEDNESDAY, done=line).empty is False
    assert WorkReport(day=WEDNESDAY, moved=line).empty is False


# --- what is read -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )
    # Holidays are test_days_off.py's; one test below puts one back.
    monkeypatch.setattr(work_report.days_off, "is_public_holiday", lambda *_, **__: False)
    monkeypatch.setattr(
        work_report,
        "get_core_settings",
        lambda: SimpleNamespace(web_base_url="https://autune.example/"),
    )


@pytest.fixture
def afternoon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever the clock says, it is a working afternoon of today: the window
    is ``report_day``'s own test."""
    monkeypatch.setattr(work_report, "report_day", lambda moment: reminders.korean_day(moment))


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
        s.add(Team(id="team_1", name="플랫폼팀"))
        s.add(Team(id="team_2", name="디자인팀"))
        for team in ("team_1", "team_2"):
            s.add(Meeting(id=f"mtg_{team}", team_id=team, title=f"{team} 회의"))
        s.add(TeamMember(team_id="team_1", user_id="user_kim"))
        s.add(TeamMember(team_id="team_2", user_id="user_kim"))
        s.add(TeamMember(team_id="team_1", user_id="user_lee"))
        day = today()
        rows = [
            # id, meeting, assignee, status, text, due
            ("act_a", "mtg_team_1", "user_kim", "todo", "보고서 쓰기", None),
            ("act_b", "mtg_team_1", "user_kim", "todo", "배포 점검", None),
            ("act_doing", "mtg_team_1", "user_kim", "in_progress", "전부터 하던 일", None),
            ("act_today", "mtg_team_1", "user_kim", "todo", "오늘까지인 일", day),
            ("act_late", "mtg_team_1", "user_kim", "todo", "늦은 일", day - timedelta(days=3)),
            ("act_later", "mtg_team_1", "user_kim", "todo", "나중 일", day + timedelta(days=9)),
            ("act_draft", "mtg_team_1", "user_kim", "needs_confirmation", "확인 전 일", None),
            ("act_other", "mtg_team_2", "user_kim", "todo", "다른 팀 일", None),
            ("act_lee", "mtg_team_1", "user_lee", "todo", "이 님의 일", None),
            ("act_gone", "mtg_team_1", "user_gone", "todo", "팀에 없는 사람 일", None),
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


def move(session: Session, item_id: str, status: ActionStatus) -> None:
    """A person moves a card on the board: the writer of the edit this reads."""
    item = session.get(ExtActionItem, item_id)
    assert item is not None
    service.update_action_item(session, item, ActionItemUpdate(status=status))
    session.flush()


def kim(day: date | None = None, team: str = "team_1") -> WorkReportOwed:
    return WorkReportOwed(user_id="user_kim", team_id=team, day=day or today())


def said(lines: object) -> list[str]:
    return [line.description for line in lines]  # type: ignore[attr-defined]


def test_the_four_parts_and_the_count_each_item_once(session: Session) -> None:
    move(session, "act_a", ActionStatus.DONE)
    move(session, "act_b", ActionStatus.IN_PROGRESS)

    report = work_report.report_content(session, kim(), now=now())

    assert report.team_name == "플랫폼팀"
    assert said(report.done) == ["보고서 쓰기"]
    assert said(report.moved) == ["배포 점검"]
    assert sorted(said(report.carried)) == ["오늘까지인 일", "전부터 하던 일"]
    assert said(report.late) == ["늦은 일"]
    assert report.others == 1, "나중 일: a count, not a line; the unconfirmed draft is no work yet"
    assert report.empty is False


def test_a_late_item_moved_today_is_todays_work_and_keeps_its_date(session: Session) -> None:
    move(session, "act_late", ActionStatus.IN_PROGRESS)

    report = work_report.report_content(session, kim(), now=now())

    assert said(report.moved) == ["늦은 일"]
    assert report.late == []
    assert "• 늦은 일 (기한 " in build_text(report, board_url=BOARD)


def test_an_edit_to_another_field_is_not_progress(session: Session) -> None:
    item = session.get(ExtActionItem, "act_doing")
    assert item is not None
    service.update_action_item(
        session, item, ActionItemUpdate(due_date=today() + timedelta(days=2))
    )
    session.flush()

    report = work_report.report_content(session, kim(), now=now())

    assert report.moved == [] and report.done == []
    assert report.empty is True


def test_a_status_changed_before_today_is_not_todays(session: Session) -> None:
    """Finished yesterday, in progress since last week: not this
    report's. The edit rows are written by hand -- nothing writes in the past."""
    for item_id, status in (("act_a", "done"), ("act_b", "in_progress")):
        item = session.get(ExtActionItem, item_id)
        assert item is not None
        item.status = status
        session.add(
            ExtEditEvent(
                meeting_id=item.meeting_id,
                action_item_id=item_id,
                kind="edited",
                fields="status",
                created_at=work_report.day_start(today()) - timedelta(minutes=1),
            )
        )
    session.flush()

    report = work_report.report_content(session, kim(), now=now())

    assert report.done == [] and report.moved == []
    assert "배포 점검" in said(report.carried), "in progress from before goes on to tomorrow"


def test_a_card_moved_and_moved_back_is_neither(session: Session) -> None:
    """The log says the status was edited, not to what: the row's status now
    is where it ended up."""
    move(session, "act_a", ActionStatus.IN_PROGRESS)
    move(session, "act_a", ActionStatus.TODO)

    report = work_report.report_content(session, kim(), now=now())

    assert report.done == [] and report.moved == []


def test_a_draft_confirmed_today_is_not_called_progress(session: Session) -> None:
    move(session, "act_draft", ActionStatus.TODO)

    report = work_report.report_content(session, kim(), now=now())

    assert report.done == [] and report.moved == []
    assert report.others == 4


def test_it_is_the_persons_own_items_on_that_team_and_nobody_elses(session: Session) -> None:
    move(session, "act_a", ActionStatus.DONE)
    move(session, "act_other", ActionStatus.DONE)
    move(session, "act_lee", ActionStatus.DONE)
    move(session, "act_gone", ActionStatus.DONE)

    one = work_report.report_content(session, kim(), now=now())
    two = work_report.report_content(session, kim(team="team_2"), now=now())

    assert said(one.done) == ["보고서 쓰기"]
    assert (two.team_name, said(two.done)) == ("디자인팀", ["다른 팀 일"])
    text = build_text(one, board_url=BOARD)
    assert "이 님의 일" not in text and "팀에 없는 사람 일" not in text and "다른 팀 일" not in text


def test_a_meeting_past_its_retention_window_is_left_out(session: Session) -> None:
    move(session, "act_a", ActionStatus.DONE)
    meeting = session.get(Meeting, "mtg_team_1")
    assert meeting is not None
    meeting.expires_at = now() - timedelta(days=1)
    session.flush()

    assert work_report.report_content(session, kim(), now=now()).empty is True


# --- who is owed ------------------------------------------------------------------


def owed(session: Session) -> list[tuple[str, str]]:
    return [(r.user_id, r.team_id) for r in work_report.reports_to_send(session, now=now())]


@pytest.mark.usefixtures("afternoon")
def test_one_is_owed_per_person_and_team_with_something_of_today(session: Session) -> None:
    assert owed(session) == [], "open work alone is no report"

    move(session, "act_a", ActionStatus.DONE)
    move(session, "act_other", ActionStatus.IN_PROGRESS)
    move(session, "act_lee", ActionStatus.DONE)
    move(session, "act_gone", ActionStatus.DONE)

    assert owed(session) == [
        ("user_kim", "team_1"),
        ("user_kim", "team_2"),
        ("user_lee", "team_1"),
    ], "and nobody for the item of somebody who is not on the team"
    assert all(r.day == today() for r in work_report.reports_to_send(session, now=now()))


def test_nothing_is_owed_outside_a_working_afternoon(session: Session) -> None:
    move(session, "act_a", ActionStatus.DONE)

    saturday_afternoon = datetime(2026, 10, 10, 16, 30, tzinfo=reminders.KST)
    wednesday_morning = datetime(2026, 10, 7, 10, 0, tzinfo=reminders.KST)

    assert work_report.reports_to_send(session, now=saturday_afternoon) == []
    assert work_report.reports_to_send(session, now=wednesday_morning) == []


@pytest.mark.usefixtures("afternoon")
def test_nothing_is_owed_on_a_public_holiday(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    move(session, "act_a", ActionStatus.DONE)
    monkeypatch.setattr(work_report.days_off, "is_public_holiday", lambda *_, **__: True)

    assert owed(session) == []


@pytest.mark.usefixtures("afternoon")
def test_reminders_off_or_a_leave_day_means_no_report(session: Session) -> None:
    move(session, "act_a", ActionStatus.DONE)
    move(session, "act_lee", ActionStatus.DONE)

    service.set_due_reminders(session, "user_kim", on=False, now=now())
    assert owed(session) == [("user_lee", "team_1")]

    service.set_due_reminders(session, "user_kim", on=True, now=now())
    service.set_notification_pause(
        session, "user_lee", starts_on=today(), ends_on=today() + timedelta(days=1), now=now()
    )
    assert owed(session) == [("user_kim", "team_1")]


# --- once, and to its person --------------------------------------------------------


class FakeSlack:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_dm(self, user_id: str, text: str) -> str:
        self.sent.append((user_id, text))
        return "17000.1"


def test_it_is_claimed_then_sent_to_its_person_and_only_once(session: Session) -> None:
    move(session, "act_a", ActionStatus.DONE)
    slack = FakeSlack()

    assert work_report.send_report(session, slack, kim(), now=now()) is True
    assert work_report.send_report(session, slack, kim(), now=now()) is False

    ((to, text),) = slack.sent
    assert to == "user_kim"
    assert text.split("\n")[:4] == [
        "오늘 업무 보고 초안입니다. 고쳐서 팀에 붙여 넣으셔도 됩니다.",
        f"플랫폼팀 업무 보고 ({today().isoformat()})",
        "끝낸 일",
        "• 보고서 쓰기 · team_1 회의",
    ]
    assert text.split("\n")[-1] == BOARD
    (row,) = session.query(ExtWorkReport).all()
    assert (row.user_id, row.team_id, row.day) == ("user_kim", "team_1", today())


@pytest.mark.usefixtures("afternoon")
def test_a_report_that_went_is_not_owed_again_and_leaves_the_morning_dm_alone(
    session: Session,
) -> None:
    move(session, "act_a", ActionStatus.DONE)
    work_report.send_report(session, FakeSlack(), kim(), now=now())

    assert owed(session) == []
    assert session.query(autune_extraction.models.ExtDailyDigest).count() == 0


def test_nothing_to_report_sends_nothing_and_claims_nothing(session: Session) -> None:
    slack = FakeSlack()

    assert work_report.send_report(session, slack, kim(), now=now()) is False
    assert work_report.would_go(session, kim(), now=now()) is False

    assert slack.sent == []
    assert session.query(ExtWorkReport).count() == 0


def test_a_choice_made_after_the_list_was_drawn_up_still_holds(session: Session) -> None:
    """The person's switch and pause are read again at the send."""
    move(session, "act_a", ActionStatus.DONE)
    slack = FakeSlack()
    assert work_report.would_go(session, kim(), now=now()) is True

    service.set_due_reminders(session, "user_kim", on=False, now=now())

    assert work_report.would_go(session, kim(), now=now()) is False
    assert work_report.send_report(session, slack, kim(), now=now()) is False
    assert slack.sent == [] and session.query(ExtWorkReport).count() == 0


# --- the task -----------------------------------------------------------------------


def test_the_setting_is_off_by_default_and_the_task_then_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ExtractionSettings(_env_file=None).work_report is False  # type: ignore[call-arg]
    monkeypatch.setattr(tasks, "get_settings", lambda: SimpleNamespace(work_report=False))
    monkeypatch.setattr(tasks, "session_scope", None)  # would fail if it were opened

    assert tasks.send_work_reports() == []


@dataclass
class _Config:
    secret: str

    def require_secret(self) -> str:
        return self.secret


@pytest.fixture
def checked_slack(
    session: Session, monkeypatch: pytest.MonkeyPatch, afternoon: None
) -> CheckedSlack:
    """The task with this session, connected teams, the switch on, a working
    afternoon, and the fake that runs the outbound check -- the one that refuses
    a phone number, as the real client does."""

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
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None, work_report=True),  # type: ignore[call-arg]
    )
    return fake


def test_the_task_sends_each_person_their_own_and_nothing_twice(
    session: Session, checked_slack: CheckedSlack
) -> None:
    move(session, "act_a", ActionStatus.DONE)
    move(session, "act_lee", ActionStatus.IN_PROGRESS)
    session.commit()

    assert sorted(tasks.send_work_reports()) == ["user_kim", "user_lee"]

    by_person = {m.channel: m.text for m in checked_slack.sent}
    assert sorted(by_person) == ["user_kim", "user_lee"]
    assert "보고서 쓰기" in by_person["user_kim"] and "이 님의 일" not in by_person["user_kim"]
    assert "이 님의 일" in by_person["user_lee"] and "보고서 쓰기" not in by_person["user_lee"]

    assert tasks.send_work_reports() == []
    assert len(checked_slack.sent) == 2


def test_a_refused_report_is_raised_after_the_others_go_and_not_tried_again(
    session: Session, checked_slack: CheckedSlack
) -> None:
    """The outbound check refuses one person's text. The other still gets
    theirs, the refusal is raised, not swallowed -- and the day's claim is
    kept, so the next run does not refuse the same text again."""
    move(session, "act_a", ActionStatus.DONE)
    move(session, "act_lee", ActionStatus.DONE)
    item = session.get(ExtActionItem, "act_a")
    assert item is not None
    item.description = "010-1234-5678로 전화하기"
    session.commit()

    with pytest.raises(PrivacyViolationError, match="user_kim"):
        tasks.send_work_reports()

    assert [m.channel for m in checked_slack.sent] == ["user_lee"]
    assert not any("010-1234-5678" in m.text for m in checked_slack.sent)
    assert sorted((r.user_id, r.team_id) for r in session.query(ExtWorkReport)) == [
        ("user_kim", "team_1"),  # refused, settled for the day
        ("user_lee", "team_1"),
    ]

    assert tasks.send_work_reports() == [], "nothing owed, nothing refused, nothing raised"
    assert len(checked_slack.sent) == 1


def test_a_report_slack_did_not_take_stays_owed(
    session: Session, checked_slack: CheckedSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a refusal is settled. A send that failed takes its claim back and
    is tried again; a person with no linked account is skipped, not failed."""
    move(session, "act_a", ActionStatus.DONE)
    move(session, "act_lee", ActionStatus.DONE)
    session.commit()

    def send_dm(user_id: str, text: str) -> str:
        if user_id == "user_kim":
            raise TransientIntegrationError("slack is down")
        raise SlackRecipientNotLinkedError("no linked account")

    monkeypatch.setattr(checked_slack, "send_dm", send_dm)

    with capture_logs() as logs:
        assert tasks.send_work_reports() == []

    assert session.query(ExtWorkReport).count() == 0
    failed = [entry for entry in logs if entry["event"] == "extraction_work_report_failed"]
    assert [(e["user_id"], e["reason"]) for e in failed] == [
        ("user_kim", "TransientIntegrationError")
    ]
    assert not any("보고서 쓰기" in str(entry) for entry in logs), "no item text in a log line"
    assert [r.user_id for r in work_report.reports_to_send(session, now=now())] == [
        "user_kim",
        "user_lee",
    ]


def test_a_team_without_slack_is_skipped(
    session: Session, checked_slack: CheckedSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    move(session, "act_a", ActionStatus.DONE)
    session.commit()
    monkeypatch.setattr(tasks, "load_integration", lambda s, team, service: None)

    assert tasks.send_work_reports() == []
    assert checked_slack.sent == [] and session.query(ExtWorkReport).count() == 0


def test_the_settings_screen_is_told_whether_this_server_sends_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        router,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None, work_report=True),  # type: ignore[call-arg]
    )
    assert router._reminder_setting(True).work_report_here is True

    monkeypatch.setattr(
        router,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )
    assert router._reminder_setting(True).work_report_here is False
