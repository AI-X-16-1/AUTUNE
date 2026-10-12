"""GET /api/extraction/since-last-meeting: what moved on a team's board since
its last analysed meeting, read at the request and stored nowhere.

SQLite in memory and the router on a bare app, as ``test_jira_issues`` does.
Every status here is set through the writer a person's move goes through
(``service.update_action_item``, ``close_without_finishing``,
``create_action_item``), so the events read are the ones the board records. An
event is dated by the database at the write; ``ago`` moves one back to stand
for a move made earlier.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_contracts.enums import ActionStatus
from autune_core import AutuneError, Base, Meeting, Team, TeamMember, User, get_session
from autune_extraction import reminders, service, since_last_meeting
from autune_extraction.models import ExtActionItem, ExtEditEvent
from autune_extraction.router import router
from autune_extraction.schemas import ActionItemCreate, ActionItemUpdate

from .conftest import sign_in

PREFIX = "/api/extraction"
MINE, THEIRS, FRESH = "team_1", "team_other", "team_fresh"
OWNER, OWNER_NAME = "user_kim", "김가나"


def now() -> datetime:
    return datetime.now(UTC)


def today() -> date:
    return reminders.korean_day(now())


LAST_HELD = timedelta(days=2)
"""How long ago ``mtg_last``, the team's last analysed meeting, was held."""


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
        for team_id, name in ((MINE, "제품팀"), (THEIRS, "남의 팀"), (FRESH, "새 팀")):
            s.add(Team(id=team_id, name=name))
        s.add(User(id=OWNER, email="kim@example.com", display_name=OWNER_NAME))
        s.add(TeamMember(team_id=MINE, user_id=OWNER))
        # Somebody is on the other team. Without a member there, "only the
        # caller's teams" would hold for any query over ``team_members``.
        s.add(TeamMember(team_id=THEIRS, user_id="user_stranger"))
        moment = now()
        meetings = [
            # id, team, status, held
            ("mtg_old", MINE, "complete", moment - timedelta(days=9)),
            ("mtg_last", MINE, "complete", moment - LAST_HELD),
            ("mtg_live", MINE, "analyzing", moment - timedelta(minutes=20)),
            ("mtg_next", MINE, "scheduled", moment + timedelta(days=1)),
            ("mtg_theirs", THEIRS, "complete", moment - LAST_HELD),
            ("mtg_fresh", FRESH, "scheduled", moment + timedelta(days=1)),
        ]
        for meeting_id, team, status, held in meetings:
            s.add(
                Meeting(
                    id=meeting_id,
                    team_id=team,
                    title=f"{meeting_id} 회의",
                    status=status,
                    started_at=held,
                )
            )
        s.flush()
        yield s


def item(
    session: Session,
    item_id: str,
    *,
    status: str = "todo",
    meeting: str = "mtg_old",
    due: date | None = None,
) -> ExtActionItem:
    """An item as the pipeline leaves it: no event of its own."""
    row = ExtActionItem(
        id=item_id,
        meeting_id=meeting,
        description=f"{item_id} 하기",
        assignee_id=OWNER,
        status=status,
        due_date=due,
        confidence=0.9,
        origin="model",
    )
    session.add(row)
    session.flush()
    return row


def _backdate(session: Session, item_id: str, ago: timedelta | None) -> None:
    if ago is None:
        return
    latest = session.scalars(
        select(ExtEditEvent)
        .where(ExtEditEvent.action_item_id == item_id)
        .order_by(ExtEditEvent.id.desc())
        .limit(1)
    ).one()
    latest.created_at = now() - ago
    session.flush()


def move(
    session: Session, item_id: str, status: ActionStatus, *, ago: timedelta | None = None
) -> None:
    """A person moves a card on the board: the writer of the edit this reads."""
    row = session.get(ExtActionItem, item_id)
    assert row is not None
    service.update_action_item(session, row, ActionItemUpdate(status=status))
    session.flush()
    _backdate(session, item_id, ago)


def close(session: Session, item_id: str, *, ago: timedelta | None = None) -> None:
    row = session.get(ExtActionItem, item_id)
    assert row is not None
    assert service.close_without_finishing(session, row)
    session.flush()
    _backdate(session, item_id, ago)


def read(session: Session, team: str = MINE, **kwargs: datetime):
    return since_last_meeting.summary(session, team, **kwargs)


BEFORE = timedelta(days=5)
"""Before ``mtg_last``, after ``mtg_old``."""


def test_each_part_holds_what_moved_since_the_last_meeting_and_each_item_once(
    session: Session,
) -> None:
    for item_id in ("act_done", "act_closed", "act_doing", "act_still", "act_done_before"):
        item(session, item_id)
    item(session, "act_new", status="needs_confirmation", meeting="mtg_last")
    item(session, "act_draft", status="needs_confirmation", meeting="mtg_last")
    item(session, "act_late", due=today() - timedelta(days=3))
    item(session, "act_due_today", due=today())
    move(session, "act_done", ActionStatus.DONE)
    close(session, "act_closed")
    move(session, "act_doing", ActionStatus.IN_PROGRESS)
    move(session, "act_new", ActionStatus.TODO)
    move(session, "act_done_before", ActionStatus.DONE, ago=BEFORE)
    typed = service.create_action_item(
        session, ActionItemCreate(meeting_id="mtg_last", description="손으로 넣은 일")
    )

    answer = read(session)

    assert answer.state == "ok"
    assert (answer.meeting_id, answer.meeting_title) == ("mtg_last", "mtg_last 회의")
    assert answer.finished == ["act_done"]
    assert answer.closed == ["act_closed"]
    assert answer.late == ["act_late"]
    assert answer.started == ["act_doing"]
    assert sorted(answer.created) == sorted(["act_new", typed.id])
    # Every open item of the team: the four listed and the two that only sit
    # there. Not the one still waiting to be confirmed, and not a finished one.
    assert answer.open == 6
    listed = answer.finished + answer.closed + answer.late + answer.started + answer.created
    assert len(listed) == len(set(listed))


def test_a_move_made_before_the_last_meeting_is_not_since_it(session: Session) -> None:
    for item_id in ("act_done", "act_closed", "act_doing", "act_confirmed"):
        item(session, item_id)
    move(session, "act_done", ActionStatus.DONE, ago=BEFORE)
    close(session, "act_closed", ago=BEFORE)
    move(session, "act_doing", ActionStatus.IN_PROGRESS, ago=BEFORE)
    move(session, "act_confirmed", ActionStatus.TODO, ago=BEFORE)

    answer = read(session)

    assert (answer.finished, answer.closed, answer.started, answer.created) == ([], [], [], [])
    assert answer.open == 2


def test_an_edit_that_did_not_touch_the_status_moved_nothing(session: Session) -> None:
    item(session, "act_done_before")
    item(session, "act_doing_before")
    move(session, "act_done_before", ActionStatus.DONE, ago=BEFORE)
    move(session, "act_doing_before", ActionStatus.IN_PROGRESS, ago=BEFORE)
    for item_id in ("act_done_before", "act_doing_before"):
        row = session.get(ExtActionItem, item_id)
        assert row is not None
        service.update_action_item(
            session, row, ActionItemUpdate(due_date=today() + timedelta(days=7))
        )
    session.flush()

    answer = read(session)

    assert (answer.finished, answer.started) == ([], [])


def test_since_is_the_instant_the_meeting_was_held_not_its_day(session: Session) -> None:
    item(session, "act_just_before")
    item(session, "act_just_after")
    move(session, "act_just_before", ActionStatus.DONE, ago=LAST_HELD + timedelta(minutes=1))
    move(session, "act_just_after", ActionStatus.DONE, ago=LAST_HELD - timedelta(minutes=1))

    assert read(session).finished == ["act_just_after"]


def test_an_item_finished_and_reopened_since_is_where_it_is_now(session: Session) -> None:
    item(session, "act_back_doing")
    item(session, "act_back_todo")
    move(session, "act_back_todo", ActionStatus.IN_PROGRESS, ago=BEFORE)
    for item_id in ("act_back_doing", "act_back_todo"):
        move(session, item_id, ActionStatus.DONE)
    move(session, "act_back_doing", ActionStatus.IN_PROGRESS)
    move(session, "act_back_todo", ActionStatus.TODO)

    answer = read(session)

    assert answer.finished == []
    assert answer.started == ["act_back_doing"]
    # On the board before the last meeting and back in 진행 전: open, not new.
    assert answer.created == []
    assert answer.open == 2


def test_a_closed_item_reopened_and_finished_is_finished(session: Session) -> None:
    item(session, "act_a")
    close(session, "act_a")
    move(session, "act_a", ActionStatus.TODO)
    move(session, "act_a", ActionStatus.DONE)

    answer = read(session)

    assert (answer.finished, answer.closed) == (["act_a"], [])


def test_a_late_item_is_late_whatever_else_happened_to_it(session: Session) -> None:
    item(session, "act_late_doing", due=today() - timedelta(days=1))
    move(session, "act_late_doing", ActionStatus.IN_PROGRESS)

    answer = read(session)

    assert (answer.late, answer.started) == (["act_late_doing"], [])


def test_late_is_by_koreas_day_on_a_server_that_keeps_utc(session: Session) -> None:
    # 15:30 UTC is half past midnight in Korea, a day later than UTC's date.
    utc_day = (now() + timedelta(days=1)).date()
    moment = datetime(utc_day.year, utc_day.month, utc_day.day, 15, 30, tzinfo=UTC)
    item(session, "act_due_utc_day", due=utc_day)
    item(session, "act_due_korean_day", due=utc_day + timedelta(days=1))

    assert read(session, now=moment).late == ["act_due_utc_day"]


def test_a_list_runs_by_due_date_with_the_undated_last(session: Session) -> None:
    item(session, "act_none")
    item(session, "act_far", due=today() + timedelta(days=9))
    item(session, "act_near", due=today() + timedelta(days=1))
    for item_id in ("act_none", "act_far", "act_near"):
        move(session, item_id, ActionStatus.DONE)

    assert read(session).finished == ["act_near", "act_far", "act_none"]


def test_a_meeting_under_way_or_not_yet_held_is_not_the_last_one(session: Session) -> None:
    answer = read(session)

    assert answer.meeting_id == "mtg_last"
    assert answer.since is not None
    assert abs(answer.since - (now() - LAST_HELD)) < timedelta(minutes=1)


def test_an_analysed_meeting_dated_after_now_is_not_yet_the_last_one(session: Session) -> None:
    # A recording uploaded ahead of the hour the meeting was put on the calendar.
    session.add(
        Meeting(
            id="mtg_ahead",
            team_id=MINE,
            title="당겨 한 회의",
            status="complete",
            started_at=now() + timedelta(hours=3),
        )
    )
    session.flush()

    assert read(session).meeting_id == "mtg_last"


def test_a_team_with_no_analysed_meeting_gets_no_parts(session: Session) -> None:
    item(session, "act_fresh", meeting="mtg_fresh")
    move(session, "act_fresh", ActionStatus.DONE)

    answer = read(session, FRESH)

    assert answer.state == "no_meeting"
    assert (answer.meeting_id, answer.meeting_title, answer.since) == (None, None, None)
    assert (answer.finished, answer.closed, answer.late, answer.started, answer.created) == (
        [],
        [],
        [],
        [],
        [],
    )
    assert answer.open == 0


def test_a_last_meeting_past_its_retention_gives_way_to_the_one_before(
    session: Session,
) -> None:
    item(session, "act_kept")
    item(session, "act_expired", meeting="mtg_last")
    # Between the two meetings: since the older one, before the newer.
    move(session, "act_kept", ActionStatus.DONE, ago=BEFORE)
    move(session, "act_expired", ActionStatus.DONE)
    assert read(session).finished == ["act_expired"]

    last = session.get(Meeting, "mtg_last")
    assert last is not None
    last.expires_at = now() - timedelta(hours=1)
    session.flush()
    answer = read(session)

    assert answer.meeting_id == "mtg_old"
    assert answer.finished == ["act_kept"]


def test_another_teams_items_are_not_in_a_teams_answer(session: Session) -> None:
    item(session, "act_theirs", meeting="mtg_theirs")
    item(session, "act_theirs_late", meeting="mtg_theirs", due=today() - timedelta(days=2))
    move(session, "act_theirs", ActionStatus.DONE)

    mine, theirs = read(session), read(session, THEIRS)

    # Neither what moved nor what only sits there: a late item needs no event
    # to be listed, and an open one none to be counted.
    assert (mine.finished, mine.late, mine.open) == ([], [], 0)
    assert (theirs.finished, theirs.late, theirs.open) == (["act_theirs"], ["act_theirs_late"], 1)


# -- the route --------------------------------------------------------------


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session, team_id=MINE)
    yield TestClient(app)


def test_the_route_answers_for_each_of_the_callers_teams_and_no_other(
    client: TestClient, session: Session
) -> None:
    session.add(TeamMember(team_id=FRESH, user_id="user_reader"))
    item(session, "act_mine")
    item(session, "act_theirs", meeting="mtg_theirs")
    move(session, "act_mine", ActionStatus.DONE)
    move(session, "act_theirs", ActionStatus.DONE)

    body = client.get(f"{PREFIX}/since-last-meeting").json()

    by_team = {one["team_id"]: one for one in body}
    assert set(by_team) == {MINE, FRESH}
    assert by_team[MINE]["finished"] == ["act_mine"]
    assert by_team[MINE]["team_name"] == "제품팀"
    assert by_team[FRESH]["state"] == "no_meeting"


def test_a_named_team_or_meeting_answers_for_that_team_members_only(
    client: TestClient, session: Session
) -> None:
    item(session, "act_mine")
    move(session, "act_mine", ActionStatus.DONE)

    for params in ({"team_id": MINE}, {"meeting_id": "mtg_old"}):
        body = client.get(f"{PREFIX}/since-last-meeting", params=params).json()
        assert [one["team_id"] for one in body] == [MINE]
        assert body[0]["finished"] == ["act_mine"]
    for params in ({"team_id": THEIRS}, {"team_id": "team_nope"}, {"meeting_id": "mtg_theirs"}):
        assert client.get(f"{PREFIX}/since-last-meeting", params=params).status_code == 404


def test_the_answer_names_nobody_counts_nobody_and_leaves_nothing_behind(
    client: TestClient, session: Session
) -> None:
    item(session, "act_mine")
    move(session, "act_mine", ActionStatus.DONE)
    session.flush()
    events = session.scalar(select(func.count()).select_from(ExtEditEvent))

    with capture_logs() as logs:
        answer = client.get(f"{PREFIX}/since-last-meeting")

    (one,) = answer.json()
    # Ids in parts and a team total. A key added here that says who, or how
    # many of whose, is the per-person record ADR 0003 forbids.
    assert set(one) == {
        "team_id",
        "team_name",
        "state",
        "meeting_id",
        "meeting_title",
        "since",
        "finished",
        "closed",
        "late",
        "started",
        "created",
        "open",
    }
    assert OWNER not in answer.text
    assert OWNER_NAME not in answer.text
    assert logs == []
    assert session.scalar(select(func.count()).select_from(ExtEditEvent)) == events
