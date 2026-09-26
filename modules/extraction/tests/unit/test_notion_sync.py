"""Step 7 for action items: a confirmed item becomes one Notion page (#30).

SQLite in memory, ``FakeNotion`` for the workspace, the router on a bare app.
The rules under test are when a page is sent (only after a person confirms, and
only once), what it carries (the item, never the transcript), and that a team
without Notion, a broker that is down or a failed call cost the page and nothing
else.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import AutuneError, Base, Meeting, PrivacyViolationError, Utterance, get_session
from autune_core.integrations_config import IntegrationConfig
from autune_extraction import service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtEditEvent,
    ExtExternalRef,
)
from autune_extraction.router import router
from autune_integrations import PermanentIntegrationError
from autune_integrations.fakes import FakeNotion

MEETING = "mtg_1"
DATABASE = "db_actions"
PREFIX = "/api/extraction"

TABLES = [
    Meeting.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtEditEvent.__table__,
    ExtExternalRef.__table__,
]


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE", raising=False)
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
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(Meeting(id=MEETING, team_id="team_1", title="스프린트 회의"))
        session.add(
            Utterance(
                id="utt_1",
                meeting_id=MEETING,
                speaker_label="SPEAKER_00",
                start_sec=0.0,
                end_sec=2.0,
                text="제가 금요일까지 정리하겠습니다",
            )
        )
        session.flush()
        yield session


def item(session: Session, *, status: str = "todo", **fields: object) -> ExtActionItem:
    row = ExtActionItem(
        meeting_id=MEETING,
        description=str(fields.pop("description", "릴리스 노트 정리")),
        assignee_label=fields.pop("assignee_label", "김개발"),
        due_date=fields.pop("due_date", date(2026, 9, 25)),
        status=status,
        confidence=0.91,
        origin="model",
    )
    row.sources = [ExtActionItemSource(utterance_id="utt_1")]
    session.add(row)
    session.flush()
    return row


def sync(session: Session, notion: FakeNotion, action_item_id: str, **kw: object) -> object:
    return service.sync_action_item_to_notion(
        session, notion, action_item_id=action_item_id, database_id=DATABASE, **kw
    )


# --- what a page carries ---------------------------------------------------------


def test_a_confirmed_item_becomes_one_page_with_the_item_and_no_transcript(
    session: Session,
) -> None:
    notion = FakeNotion()
    row = item(session)

    ref = sync(session, notion, row.id)

    assert len(notion.pages) == 1
    database, properties = notion.pages[0]
    assert database == DATABASE
    assert properties == {
        "작업": {"title": [{"type": "text", "text": {"content": "릴리스 노트 정리"}}]},
        "담당자": {"rich_text": [{"type": "text", "text": {"content": "김개발"}}]},
        "마감일": {"date": {"start": "2026-09-25"}},
        "상태": {"select": {"name": "todo"}},
        "신뢰도": {"number": 0.91},
        "회의": {"rich_text": [{"type": "text", "text": {"content": "스프린트 회의"}}]},
    }
    assert "제가 금요일까지" not in repr(properties), "source utterances stay in Autune"
    stored = session.get(ExtExternalRef, (row.id, "notion"))
    assert stored is ref
    assert stored is not None
    assert stored.external_id == "page_1"
    assert stored.url == "https://www.notion.so/page_1"


def test_a_field_the_item_does_not_have_is_left_off_the_page(session: Session) -> None:
    notion = FakeNotion()
    row = item(session, assignee_label=None, due_date=None)

    sync(session, notion, row.id)

    assert set(notion.pages[0][1]) == {"작업", "상태", "신뢰도", "회의"}


def test_a_team_map_replaces_the_defaults_and_can_send_only_a_title(session: Session) -> None:
    """A team whose database has two columns gets a page with two. Merged with
    the defaults it got none: Notion refuses a page naming a property the
    database does not have. Raised in review of #294."""
    notion = FakeNotion()
    row = item(session)

    sync(session, notion, row.id, property_names={"title": "Name", "assignee": "Owner"})

    assert set(notion.pages[0][1]) == {"Name", "Owner"}


# --- when a page is sent --------------------------------------------------------


def test_an_item_still_waiting_for_confirmation_sends_nothing(session: Session) -> None:
    notion = FakeNotion()
    row = item(session, status="needs_confirmation")

    assert sync(session, notion, row.id) is None
    assert notion.pages == []
    assert session.scalars(select(ExtExternalRef)).all() == []


def test_the_second_sync_of_an_item_sends_nothing(session: Session) -> None:
    """A confirmation delivered twice, or an item moved on to done: one page."""
    notion = FakeNotion()
    row = item(session)

    sync(session, notion, row.id)
    row.status = "done"
    assert sync(session, notion, row.id) is None

    assert len(notion.pages) == 1


def test_a_failed_call_takes_the_claim_back_so_a_later_sync_can_send(session: Session) -> None:
    row = item(session)
    session.commit()

    class Refusing(FakeNotion):
        def create_page(self, database_id: str, properties: dict) -> str:
            raise PermanentIntegrationError("notion rejected the page: unknown property")

    with pytest.raises(PermanentIntegrationError):
        sync(session, Refusing(), row.id)
    session.rollback()

    notion = FakeNotion()
    sync(session, notion, row.id)
    assert len(notion.pages) == 1


def test_a_gone_item_sends_nothing(session: Session) -> None:
    assert sync(session, FakeNotion(), "act_missing") is None


def test_only_leaving_needs_confirmation_is_a_confirmation(session: Session) -> None:
    row = item(session, status="todo")

    assert service.became_confirmed("needs_confirmation", row)
    assert not service.became_confirmed("todo", row)
    row.status = "needs_confirmation"
    assert not service.became_confirmed("needs_confirmation", row)


# --- the board's edit is the trigger ---------------------------------------------


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(tasks, "sync_after_confirmation", calls.append)
    return calls


def test_confirming_on_the_board_queues_the_page_once(
    client: TestClient, session: Session, queued: list[str]
) -> None:
    row = item(session, status="needs_confirmation")

    client.patch(f"{PREFIX}/action-items/{row.id}", json={"status": "todo"})
    client.patch(f"{PREFIX}/action-items/{row.id}", json={"status": "done"})
    client.patch(f"{PREFIX}/action-items/{row.id}", json={"description": "고친 설명"})

    assert queued == [row.id]


def test_an_edit_that_keeps_the_item_unconfirmed_queues_nothing(
    client: TestClient, session: Session, queued: list[str]
) -> None:
    row = item(session, status="needs_confirmation")

    client.patch(f"{PREFIX}/action-items/{row.id}", json={"assignee_label": "박디자인"})

    assert queued == []


def test_a_notion_failure_does_not_fail_the_confirmation(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(_: str) -> None:
        raise PermanentIntegrationError("notion rejected the page")

    monkeypatch.setattr(tasks, "sync_action_item", refused)
    row = item(session, status="needs_confirmation")

    response = client.patch(f"{PREFIX}/action-items/{row.id}", json={"status": "todo"})

    assert response.status_code == 200
    assert session.get(ExtActionItem, row.id).status == "todo"  # type: ignore[union-attr]


def test_a_privacy_guard_block_does_not_crash_the_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``check_outbound`` raises ``PrivacyViolationError``, a sibling of
    ``IntegrationError`` -- not caught by the same except clause. Left
    uncaught, this would crash the FastAPI background task the confirming
    request already returned from (review of #333)."""

    def blocked(_: str) -> None:
        raise PrivacyViolationError("notion: phone number pattern found")

    monkeypatch.setattr(tasks, "sync_action_item", blocked)

    tasks.sync_after_confirmation("act_1")  # must not raise


# --- the task: the team's own Notion, or nothing ----------------------------------


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    monkeypatch.setattr(tasks, "session_scope", scope)
    return session


def test_a_team_whose_notion_has_no_action_database_is_skipped(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connected for decisions only, or a token that is gone: skipped, not a 422
    out of the request that confirmed the item. Raised in review of #294."""
    row = item(wired)
    for config in (
        IntegrationConfig(service="notion", team_id="team_1", secret="t", config={}),
        IntegrationConfig(
            service="notion", team_id="team_1", secret=None, config={"action_db_id": "db"}
        ),
    ):
        monkeypatch.setattr(tasks, "load_integration", lambda _s, _t, _n, c=config: c)
        tasks.sync_action_item(row.id)

    assert wired.scalars(select(ExtExternalRef)).all() == []


def test_the_sync_task_does_not_retry_itself() -> None:
    """A timeout can mean the page was made and the answer lost; a retry then
    makes a second one. Raised in review of #294."""
    assert not getattr(tasks.sync_action_item, "autoretry_for", ())
    assert not getattr(tasks.sync_decision, "autoretry_for", ())


def test_a_team_without_notion_is_skipped_not_failed(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "load_integration", lambda *_: None)
    row = item(wired)

    tasks.sync_action_item(row.id)

    assert wired.scalars(select(ExtExternalRef)).all() == []


def test_the_task_uses_the_teams_token_and_database(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    notion = FakeNotion()
    tokens: list[str] = []

    def client_for(token: str) -> FakeNotion:
        tokens.append(token)
        return notion

    config = IntegrationConfig(
        service="notion",
        team_id="team_1",
        secret="secret-token",
        config={"action_db_id": "db_team_1"},
    )
    monkeypatch.setattr(tasks, "load_integration", lambda _s, team_id, name: config)
    monkeypatch.setattr(tasks, "NotionClient", client_for)
    row = item(wired)

    tasks.sync_action_item(row.id)
    tasks.sync_action_item(row.id)

    assert tokens == ["secret-token", "secret-token"]
    assert [database for database, _ in notion.pages] == ["db_team_1"]
