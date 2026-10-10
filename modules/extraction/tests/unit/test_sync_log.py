"""S28's "동기화 기록": a team's standing failures and its latest copies, and who
is told which.

SQLite in memory. A failure is recorded by the real ``sync_after_confirmation``
over sends that raise, and a Notion page by the real
``sync_action_item_to_notion`` over ``FakeNotion``. Jira issues and calendar
events are written as rows: what is under test is the read.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import (
    AutuneError,
    Base,
    Meeting,
    Participant,
    TeamMember,
    User,
    Utterance,
    get_session,
)
from autune_extraction import service, sync_log, sync_state, tasks
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtCalendarEvent,
    ExtExternalRef,
)
from autune_extraction.router import router
from autune_integrations import PermanentIntegrationError, TransientIntegrationError
from autune_integrations.fakes import FakeNotion

from .conftest import READER, RECORD_FAILURE, SYNC_FAILED, SYNC_WENT, sign_in

MEETING = "mtg_1"
THEIRS = "mtg_other_team"
PREFIX = "/api/extraction"
KIM = "user_kim"
ECHO = "the service's own words, which may quote the item"
T0 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        s.add(Meeting(id=THEIRS, team_id="team_2", title="남의 회의"))
        s.add(User(id=KIM, email="kim@example.com", display_name="김민경"))
        s.add(TeamMember(team_id="team_1", user_id=KIM))
        s.add(
            Utterance(
                id="utt_1",
                meeting_id=MEETING,
                speaker_label="SPEAKER_00",
                start_sec=0.0,
                end_sec=2.0,
                text="제가 금요일까지 정리하겠습니다",
            )
        )
        s.flush()
        yield s


@pytest.fixture
def client(session: Session) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=PREFIX)

    @app.exception_handler(AutuneError)
    def _handle(_request: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session)
    return TestClient(app)


@pytest.fixture
def sends(session: Session, monkeypatch: pytest.MonkeyPatch) -> dict[str, BaseException | None]:
    """The real bookkeeping on this session, and three sends that each raise
    what the test puts under their name -- or go through."""

    @contextmanager
    def scope() -> Iterator[Session]:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise

    outcome: dict[str, BaseException | None] = {"notion": None, "calendar": None, "jira": None}

    def send(system: str):  # type: ignore[no-untyped-def]
        def run(_action_item_id: str) -> None:
            error = outcome[system]
            if error is not None:
                raise error

        return run

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "_sync_failed", SYNC_FAILED)
    monkeypatch.setattr(tasks, "_sync_went", SYNC_WENT)
    monkeypatch.setattr(tasks, "_record_failure", RECORD_FAILURE)
    monkeypatch.setattr(tasks, "sync_action_item", send("notion"))
    monkeypatch.setattr(tasks, "sync_action_item_calendar", send("calendar"))
    monkeypatch.setattr(tasks, "sync_action_item_jira", send("jira"))
    return outcome


def item(session: Session, item_id: str = "act_1", **fields: object) -> ExtActionItem:
    row = ExtActionItem(
        id=item_id,
        description=str(fields.pop("description", "스펙 초안 공유")),
        confidence=0.9,
        origin="model",
        **{
            "meeting_id": MEETING,
            "status": "todo",
            "assignee_id": KIM,
            "due_date": date(2026, 10, 9),
            **fields,
        },
    )
    row.sources = [ExtActionItemSource(utterance_id="utt_1")]
    session.add(row)
    session.commit()
    return row


def issue(session: Session, item_id: str, *, at: datetime, key: str | None = "KAN-1") -> None:
    """A Jira issue the item became -- or, with no key, a claim still in flight."""
    meeting_id = session.scalars(
        select(ExtActionItem.meeting_id).where(ExtActionItem.id == item_id)
    ).one()
    session.add(
        ExtExternalRef(
            action_item_id=item_id,
            system="jira",
            meeting_id=meeting_id,
            external_id=key,
            url=f"https://autune.atlassian.net/browse/{key}" if key else None,
            created_at=at,
        )
    )
    session.commit()


def event(session: Session, item_id: str, *, on: str, at: datetime = T0, made: bool = True) -> None:
    """An event on ``on``'s calendar -- or, not ``made``, the claim before one."""
    session.add(
        ExtCalendarEvent(
            action_item_id=item_id,
            meeting_id=MEETING,
            user_id=on,
            event_id=f"ev_{item_id}" if made else None,
            synced_due_date=date(2026, 10, 9),
            created_at=at,
        )
    )
    session.commit()


def read(client: TestClient, team_id: str = "team_1") -> dict[str, list[dict[str, object]]]:
    answer = client.get(f"{PREFIX}/sync-log", params={"team_id": team_id})
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


# --- failures --------------------------------------------------------------------


def test_a_failed_copy_shows_with_its_item_and_its_meeting(
    session: Session, sends: dict, client: TestClient
) -> None:
    item(session)
    sends["notion"] = PermanentIntegrationError(ECHO)
    tasks.sync_after_confirmation("act_1")

    answer = client.get(f"{PREFIX}/sync-log", params={"team_id": "team_1"})

    (failure,) = answer.json()["failures"]
    assert {k: v for k, v in failure.items() if k != "failed_at"} == {
        "action_item_id": "act_1",
        "meeting_id": MEETING,
        "meeting_title": "주간 회의",
        "description": "스펙 초안 공유",
        "system": "notion",
        "kind": "rejected",
    }
    assert failure["failed_at"]
    assert ECHO not in answer.text
    assert KIM not in answer.text, "who the item is assigned to is not part of the log"
    assert answer.json()["copies"] == []


def test_a_failure_is_gone_once_the_copy_goes_through(
    session: Session, sends: dict, client: TestClient
) -> None:
    item(session)
    sends["jira"] = TransientIntegrationError("jira timed out")
    tasks.sync_after_confirmation("act_1")
    assert [f["system"] for f in read(client)["failures"]] == ["jira"]

    sends["jira"] = None
    tasks.sync_after_confirmation("act_1")

    assert read(client)["failures"] == []


def test_a_failed_calendar_copy_is_shown_to_the_items_assignee_and_nobody_else(
    session: Session, sends: dict, client: TestClient
) -> None:
    """A calendar is one person's: that its copy failed says something about
    their account. A teammate reading the team's log is not told."""
    item(session, "act_kim", assignee_id=KIM)
    item(session, "act_mine", assignee_id=READER)
    sends["calendar"] = TransientIntegrationError("calendar timed out")
    sends["notion"] = TransientIntegrationError("notion timed out")
    tasks.sync_after_confirmation("act_kim")
    tasks.sync_after_confirmation("act_mine")

    shown = {(f["action_item_id"], f["system"]) for f in read(client)["failures"]}

    assert shown == {("act_kim", "notion"), ("act_mine", "notion"), ("act_mine", "calendar")}


def test_a_draft_with_nothing_outside_shows_no_failure(
    session: Session, client: TestClient
) -> None:
    """Back in 확인 필요 with no copy, nothing is retried for it: the board's
    card says nothing (``sync_state.failures_for``), and neither does the log."""
    item(session, status="needs_confirmation")
    sync_state.record_failure(session, "act_1", "notion", "unreachable")
    session.commit()

    assert read(client)["failures"] == []


# --- copies ----------------------------------------------------------------------


def test_a_page_that_was_made_shows_as_a_copy_with_its_link(
    session: Session, client: TestClient
) -> None:
    item(session)
    service.sync_action_item_to_notion(
        session, FakeNotion(), action_item_id="act_1", database_id="db_actions"
    )
    session.commit()
    made = session.scalars(select(ExtExternalRef)).one()

    (copy,) = read(client)["copies"]

    assert {k: v for k, v in copy.items() if k != "copied_at"} == {
        "action_item_id": "act_1",
        "meeting_id": MEETING,
        "meeting_title": "주간 회의",
        "description": "스펙 초안 공유",
        "system": "notion",
        "url": made.url,
    }
    assert made.url and copy["copied_at"]


def test_a_claim_with_no_issue_yet_is_not_a_copy(session: Session, client: TestClient) -> None:
    item(session)
    issue(session, "act_1", at=T0, key=None)

    assert read(client)["copies"] == []


def test_a_calendar_event_is_shown_to_the_person_whose_calendar_holds_it(
    session: Session, client: TestClient
) -> None:
    item(session, "act_kim", assignee_id=KIM)
    item(session, "act_mine", assignee_id=READER)
    item(session, "act_claimed", assignee_id=READER)
    event(session, "act_kim", on=KIM)
    event(session, "act_mine", on=READER)
    event(session, "act_claimed", on=READER, made=False)

    copies = read(client)["copies"]

    assert [(c["action_item_id"], c["system"], c["url"]) for c in copies] == [
        ("act_mine", "calendar", None)
    ]


# --- whose team, and how much ----------------------------------------------------


def test_another_teams_rows_are_not_shown_and_its_log_is_not_there(
    session: Session, client: TestClient
) -> None:
    item(session, "act_theirs", meeting_id=THEIRS)
    sync_state.record_failure(session, "act_theirs", "notion", "rejected")
    issue(session, "act_theirs", at=T0)
    # On the reader's own calendar, and still the other team's item.
    event(session, "act_theirs", on=READER)

    assert read(client) == {"failures": [], "copies": []}
    refused = client.get(f"{PREFIX}/sync-log", params={"team_id": "team_2"})
    assert refused.status_code == 404
    assert "act_theirs" not in refused.text


def test_a_request_that_names_no_team_is_refused(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/sync-log").status_code == 422


def test_a_meeting_past_retention_shows_nothing(session: Session, client: TestClient) -> None:
    item(session)
    sync_state.record_failure(session, "act_1", "notion", "rejected")
    issue(session, "act_1", at=T0)
    event(session, "act_1", on=READER)
    assert len(read(client)["failures"]) == 1 and len(read(client)["copies"]) == 2

    meeting = session.get(Meeting, MEETING)
    assert meeting is not None
    meeting.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()

    assert read(client) == {"failures": [], "copies": []}


def test_each_list_is_newest_first_and_stops_at_its_limit(session: Session) -> None:
    for n in range(4):
        item(session, f"act_{n}", assignee_id=READER)
        sync_state.record_failure(
            session, f"act_{n}", "jira", "unreachable", now=T0 + timedelta(hours=n)
        )
    issue(session, "act_0", at=T0 + timedelta(hours=1))
    issue(session, "act_1", at=T0 + timedelta(hours=5), key="KAN-2")
    event(session, "act_2", on=READER, at=T0 + timedelta(hours=3))
    event(session, "act_3", on=READER, at=T0)

    log = sync_log.team_sync_log(session, team_id="team_1", reader_id=READER, limit=3)

    assert [f.action_item_id for f in log.failures] == ["act_3", "act_2", "act_1"]
    assert [(c.action_item_id, c.system) for c in log.copies] == [
        ("act_1", "jira"),
        ("act_2", "calendar"),
        ("act_0", "jira"),
    ]


def test_the_drawer_gets_thirty_of_each_by_default(session: Session, client: TestClient) -> None:
    for n in range(sync_log.LIMIT + 2):
        item(session, f"act_{n:02}")
        sync_state.record_failure(
            session, f"act_{n:02}", "notion", "rejected", now=T0 + timedelta(minutes=n)
        )
        issue(session, f"act_{n:02}", at=T0 + timedelta(minutes=n), key=f"KAN-{n}")

    log = read(client)

    assert sync_log.LIMIT == 30
    assert len(log["failures"]) == 30 and len(log["copies"]) == 30
    assert log["failures"][0]["action_item_id"] == "act_31"
    assert log["copies"][-1]["action_item_id"] == "act_02"
