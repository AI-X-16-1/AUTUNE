"""A copy that failed is kept (its kind and time), shown, and can be retried; and
the detail says why an item has no calendar event (#680).

SQLite in memory. The three sends are stand-ins that succeed or raise: what is
under test is what ``sync_after_confirmation`` keeps afterwards, what the
routes answer with, and who is told what.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import (
    AutuneError,
    Base,
    Meeting,
    Participant,
    PrivacyViolationError,
    TeamMember,
    User,
    Utterance,
    get_session,
)
from autune_core.oauth.atlassian import JiraReconnectRequiredError
from autune_extraction import sync_state, tasks
from autune_extraction.models import ExtActionItem, ExtCalendarEvent, ExtSyncFailure
from autune_extraction.router import router
from autune_integrations import (
    PermanentIntegrationError,
    ReconnectRequiredError,
    TransientIntegrationError,
)

from .conftest import READER, SYNC_FAILED, SYNC_WENT, sign_in

MEETING = "mtg_1"
PREFIX = "/api/extraction"
KIM = "user_kim"
ECHO = "the service's own words, which may quote the item"


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
        s.add(User(id=KIM, email="kim@example.com", display_name="김민경"))
        s.add(TeamMember(team_id="team_1", user_id=KIM))
        s.add(User(id="user_out", email="out@example.com", display_name="남"))
        s.flush()
        yield s


def item(session: Session, item_id: str = "act_1", **fields: object) -> ExtActionItem:
    row = ExtActionItem(
        id=item_id,
        meeting_id=MEETING,
        description="스펙 초안 공유",
        confidence=0.9,
        origin="model",
        **{"status": "todo", "assignee_id": KIM, "due_date": date(2026, 10, 9), **fields},
    )
    session.add(row)
    session.commit()
    return row


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

    def send(system: str) -> Callable[[str], None]:
        def run(_action_item_id: str) -> None:
            if outcome[system] is not None:
                raise outcome[system]  # type: ignore[misc]

        return run

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "_sync_failed", SYNC_FAILED)
    monkeypatch.setattr(tasks, "_sync_went", SYNC_WENT)
    monkeypatch.setattr(tasks, "sync_action_item", send("notion"))
    monkeypatch.setattr(tasks, "sync_action_item_calendar", send("calendar"))
    monkeypatch.setattr(tasks, "sync_action_item_jira", send("jira"))
    return outcome


def kept(session: Session) -> dict[str, str]:
    session.expire_all()
    return {row.system: row.kind for row in session.scalars(select(ExtSyncFailure))}


# --- what is kept ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (TransientIntegrationError("notion timed out"), "unreachable"),
        (PermanentIntegrationError("notion rejected the request with 400"), "rejected"),
        (ReconnectRequiredError("the grant was refused"), "reconnect"),
        (PrivacyViolationError("unmasked value"), "privacy"),
    ],
)
def test_a_failed_copy_is_kept_by_its_kind(
    session: Session, sends: dict, error: BaseException, kind: str
) -> None:
    item(session)
    sends["notion"] = error

    tasks.sync_after_confirmation("act_1")

    assert kept(session) == {"notion": kind}


def test_each_system_fails_by_itself(session: Session, sends: dict) -> None:
    item(session)
    sends["calendar"] = TransientIntegrationError("calendar timed out")
    sends["jira"] = JiraReconnectRequiredError("refused")

    tasks.sync_after_confirmation("act_1")

    assert kept(session) == {"calendar": "unreachable", "jira": "reconnect"}


def test_what_is_kept_is_a_kind_and_a_time_and_nothing_the_service_said(
    session: Session, sends: dict
) -> None:
    """The service's message may echo what was sent. It is not stored, and the
    row has no column it could be stored in."""
    item(session)
    sends["jira"] = PermanentIntegrationError(ECHO)

    with capture_logs() as logs:
        tasks.sync_after_confirmation("act_1")

    assert {c.name for c in ExtSyncFailure.__table__.columns} == {
        "action_item_id",
        "system",
        "kind",
        "failed_at",
    }
    row = session.scalars(select(ExtSyncFailure)).one()
    assert ECHO not in repr([getattr(row, c.name) for c in ExtSyncFailure.__table__.columns])
    assert ECHO not in repr(logs)


def test_the_next_copy_that_goes_through_clears_it(session: Session, sends: dict) -> None:
    item(session)
    sends["notion"] = TransientIntegrationError("down")
    tasks.sync_after_confirmation("act_1")
    assert kept(session) == {"notion": "unreachable"}

    sends["notion"] = None
    tasks.sync_after_confirmation("act_1")

    assert kept(session) == {}


def test_a_second_failure_replaces_the_first(session: Session, sends: dict) -> None:
    item(session)
    sends["notion"] = TransientIntegrationError("down")
    tasks.sync_after_confirmation("act_1")
    sends["notion"] = PrivacyViolationError("unmasked value")
    tasks.sync_after_confirmation("act_1")

    assert kept(session) == {"notion": "privacy"}
    assert len(session.scalars(select(ExtSyncFailure)).all()) == 1


def test_an_item_that_is_gone_keeps_nothing_and_breaks_nothing(
    session: Session, sends: dict
) -> None:
    sends["notion"] = TransientIntegrationError("down")

    tasks.sync_after_confirmation("act_deleted_meanwhile")

    assert kept(session) == {}


def test_bookkeeping_that_cannot_be_written_never_crashes_the_sync(
    session: Session, sends: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(session)
    sends["notion"] = TransientIntegrationError("down")

    def broken(*_a: object, **_k: object) -> None:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(sync_state, "record_failure", broken)
    monkeypatch.setattr(sync_state, "clear_failure", broken)

    tasks.sync_after_confirmation("act_1")  # does not raise

    assert kept(session) == {}


# --- what the board is told ------------------------------------------------------


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session)
    yield TestClient(app)


def failure(session: Session, system: str = "jira", kind: str = "reconnect") -> None:
    session.add(
        ExtSyncFailure(
            action_item_id="act_1",
            system=system,
            kind=kind,
            failed_at=datetime(2026, 10, 2, 1, 0, tzinfo=UTC),
        )
    )
    session.commit()


def test_the_list_and_the_detail_carry_the_failure(client: TestClient, session: Session) -> None:
    item(session)
    failure(session)

    (listed,) = client.get(f"{PREFIX}/action-items", params={"meeting_id": MEETING}).json()
    detail = client.get(f"{PREFIX}/action-items/act_1").json()

    for body in (listed, detail):
        (entry,) = body["sync_failures"]
        assert (entry["system"], entry["kind"]) == ("jira", "reconnect")
        assert entry["failed_at"].startswith("2026-10-02T01:00:00")


def test_an_item_nothing_failed_for_carries_none(client: TestClient, session: Session) -> None:
    item(session)

    (listed,) = client.get(f"{PREFIX}/action-items", params={"meeting_id": MEETING}).json()

    assert listed["sync_failures"] == []


# --- retry -----------------------------------------------------------------------


def test_retry_queues_the_same_sync_an_edit_does(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(session)
    ran: list[str] = []
    monkeypatch.setattr(tasks, "sync_after_confirmation", ran.append)

    response = client.post(f"{PREFIX}/action-items/act_1/sync")

    assert response.status_code == 202
    assert response.json() == {"queued": True}
    assert ran == ["act_1"]


def test_an_item_never_confirmed_has_nothing_to_retry(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(session, status="needs_confirmation")
    ran: list[str] = []
    monkeypatch.setattr(tasks, "sync_after_confirmation", ran.append)

    response = client.post(f"{PREFIX}/action-items/act_1/sync")

    assert response.json() == {"queued": False}
    assert ran == []


def test_only_the_meetings_team_can_retry(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add(Meeting(id="mtg_theirs", team_id="team_other", title="남의 회의"))
    session.add(
        ExtActionItem(
            id="act_theirs",
            meeting_id="mtg_theirs",
            description="남의 일",
            status="todo",
            confidence=0.9,
            origin="model",
        )
    )
    session.commit()
    ran: list[str] = []
    monkeypatch.setattr(tasks, "sync_after_confirmation", ran.append)

    assert client.post(f"{PREFIX}/action-items/act_theirs/sync").status_code == 404
    assert client.post(f"{PREFIX}/action-items/act_nope/sync").status_code == 404
    assert ran == []


# --- why there is no calendar event ----------------------------------------------


def calendar(client: TestClient) -> dict:
    return client.get(f"{PREFIX}/action-items/act_1").json()["calendar"]


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"status": "needs_confirmation"}, "not_confirmed"),
        ({"due_date": None}, "no_due_date"),
        ({"assignee_id": None, "assignee_label": "민구"}, "no_account"),
        ({"assignee_id": "user_out"}, "not_on_team"),
    ],
)
def test_the_detail_names_the_first_thing_missing(
    client: TestClient, session: Session, fields: dict, reason: str
) -> None:
    item(session, **fields)

    assert calendar(client) == {"state": "none", "reason": reason}


def test_an_item_with_an_event_is_on_the_calendar(client: TestClient, session: Session) -> None:
    item(session)
    session.add(
        ExtCalendarEvent(
            action_item_id="act_1",
            meeting_id=MEETING,
            user_id=KIM,
            event_id="evt_1",
            synced_due_date=date(2026, 10, 9),
        )
    )
    session.commit()

    assert calendar(client) == {"state": "sent", "reason": None}


def test_that_the_assignee_has_not_connected_is_said_only_to_the_assignee(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether somebody connected their calendar is a fact about their own
    account. A teammate is told only that there is no event."""
    monkeypatch.setattr(sync_state, "users_with_integration", lambda _session, _service: [])
    item(session)  # assigned to KIM; the reader is somebody else
    assert calendar(client) == {"state": "none", "reason": None}

    session.get(ExtActionItem, "act_1").assignee_id = READER
    session.add(User(id=READER, email="reader@example.com", display_name="읽는 사람"))
    session.commit()
    assert calendar(client) == {"state": "none", "reason": "not_connected"}

    monkeypatch.setattr(sync_state, "users_with_integration", lambda _session, _service: [READER])
    assert calendar(client) == {"state": "none", "reason": None}
