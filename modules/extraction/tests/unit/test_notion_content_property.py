"""The 내용 property of a team's Notion databases: who gets it, when Notion is
asked, and which name map a sync is then given.

A page's title is becoming the short title, with the sentence in a text
property named 내용. A database made before that has no such property, and
Notion refuses a whole page that names one its database lacks -- so the map a
sync is given may name it only for a database known to have it.

Every row read here is written by the real writers (``notion_connect.set_up``,
``notion_setup.ensure_content``, the sync tasks); Notion alone is replaced, by
a mock transport that keeps each database's properties.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Meeting, Utterance
from autune_core.integrations_config import IntegrationConfig
from autune_extraction import notion_backfill, notion_connect, notion_setup, service, tasks
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtCalendarEvent,
    ExtDecision,
    ExtDecisionRef,
    ExtExternalCleanup,
    ExtExternalRef,
    ExtNotionTarget,
    ExtSyncFailure,
)
from autune_extraction.notion_setup import (
    ACTION_PROPERTIES,
    CONTENT_PROPERTY,
    DECISION_PROPERTIES,
    add_content_property,
    ensure_content,
    property_names,
)
from autune_extraction.service import DECISION_NOTION_PROPERTIES, NOTION_PROPERTIES
from autune_integrations.fakes import FakeNotion

from .conftest import TRASH_NOTION_PAGE

TEAM = "team_1"
MEETING = "mtg_1"
PAGE = "page-1"
TEXT = {"type": "rich_text", "rich_text": {}}

TABLES = [
    Meeting.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtDecision.__table__,
    ExtDecisionRef.__table__,
    ExtExternalCleanup.__table__,
    ExtExternalRef.__table__,
    ExtNotionTarget.__table__,
    ExtCalendarEvent.__table__,
    ExtSyncFailure.__table__,
]


class Notion:
    """Notion's page and database endpoints, as far as a setup goes. Keeps the
    properties of every database, so what one call adds the next one reads."""

    def __init__(self, databases: Mapping[str, dict[str, Any]] | None = None) -> None:
        self.databases: dict[str, dict[str, Any]] = {
            key: dict(properties) for key, properties in (databases or {}).items()
        }
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.shared: list[str] = []
        self.get_status: dict[str, int] = {}
        self.patch_status: dict[str, int] = {}
        self.down = False

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if self.down:
            raise httpx.ConnectTimeout("no answer", request=request)
        if path == "/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": page, "parent": {}, "properties": {}} for page in self.shared
                    ]
                },
            )
        if path == "/pages":
            return httpx.Response(200, json={"id": "home"})
        if path == "/databases":
            made = f"db_{len(self.databases) + 1}"
            self.databases[made] = _typed(body["properties"])
            return httpx.Response(200, json={"id": made})
        database = path.removeprefix("/databases/")
        refused = (self.get_status if request.method == "GET" else self.patch_status).get(database)
        if refused:
            return httpx.Response(refused, json={"message": "refused"})
        if database not in self.databases:
            return httpx.Response(404, json={"message": "gone"})
        if request.method == "PATCH":
            self.databases[database].update(_typed(body["properties"]))
        return httpx.Response(
            200,
            json={
                "parent": {"type": "page_id", "page_id": "home"},
                "properties": self.databases[database],
            },
        )

    def client(self, token: str = "t") -> httpx.Client:
        return httpx.Client(
            base_url=notion_setup.NOTION_API, transport=httpx.MockTransport(self._handle)
        )

    def asked(self, method: str) -> list[str]:
        """The databases asked about with ``method``, in order."""
        return [
            path.removeprefix("/databases/")
            for called, path, _ in self.calls
            if called == method and path.startswith("/databases/")
        ]


def _typed(properties: Mapping[str, Any]) -> dict[str, Any]:
    """A schema as Notion hands it back: each property with its ``type``, each
    select option with an id."""
    typed: dict[str, Any] = {}
    for name, spec in properties.items():
        kind = next(iter(spec))
        body = spec[kind]
        if kind == "select":
            body = {
                "options": [
                    {"id": f"opt_{n}", **option} for n, option in enumerate(body["options"])
                ]
            }
        typed[name] = {"type": kind, kind: body}
    return typed


def old_databases() -> dict[str, dict[str, Any]]:
    """The two databases as a setup before 내용 made them."""
    return {
        "db-a": _typed(notion_setup.schema(NOTION_PROPERTIES, status_select=True)),
        "db-d": _typed(notion_setup.schema(DECISION_NOTION_PROPERTIES, status_select=False)),
        "db-m": _typed(
            notion_setup.schema(notion_setup.MINUTES_NOTION_PROPERTIES, status_select=False)
        ),
    }


OLD_TARGET = {
    "parent_page_id": PAGE,
    "action_db_id": "db-a",
    "decision_db_id": "db-d",
    "minutes_db_id": "db-m",
}


def connection(**config: Any) -> IntegrationConfig:
    return IntegrationConfig("notion", TEAM, "ntn_token", {"workspace_id": "ws-1", **config})


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(Meeting(id=MEETING, team_id=TEAM, title="스프린트 회의"))
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


@pytest.fixture
def notion(monkeypatch: pytest.MonkeyPatch) -> Notion:
    """The team's Notion, holding the three databases of an old setup."""
    notion = Notion(old_databases())
    monkeypatch.setattr(notion_setup, "notion_client", notion.client)
    return notion


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """The setup, the sync tasks and the fill on this test's session, with the
    team connected by one click."""

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    for module in (notion_connect, tasks, notion_backfill):
        monkeypatch.setattr(module, "session_scope", scope)
        monkeypatch.setattr(module, "load_integration", lambda _s, _team, _name: connection())
    monkeypatch.setattr(tasks.backfill_notion, "delay", lambda _team: None)
    return session


def connected_before(session: Session, **target: str) -> None:
    """A team whose setup ran before 내용 existed: its row, as that setup's
    ``save_targets`` call wrote it -- ids and workspace, nothing about 내용."""
    notion_setup.save_targets(session, TEAM, {**OLD_TARGET, **target}, workspace_id="ws-1")
    session.commit()


def item(session: Session) -> ExtActionItem:
    row = ExtActionItem(
        meeting_id=MEETING,
        description="릴리스 노트 정리",
        assignee_label="김개발",
        status="todo",
        confidence=0.91,
        origin="model",
    )
    row.sources = [ExtActionItemSource(utterance_id="utt_1")]
    session.add(row)
    session.flush()
    return row


# --- one database --------------------------------------------------------------------


def test_a_database_without_the_property_is_given_it_as_text() -> None:
    notion = Notion(old_databases())
    with notion.client() as client:
        assert add_content_property(client, "db-a") is True

    assert notion.calls == [
        ("GET", "/databases/db-a", None),
        ("PATCH", "/databases/db-a", {"properties": {"내용": {"rich_text": {}}}}),
    ]
    assert notion.databases["db-a"][CONTENT_PROPERTY] == TEXT


def test_only_the_fixed_name_leaves_and_no_other_property_is_touched() -> None:
    """What is sent is the database id and one name fixed at import."""
    notion = Notion(old_databases())
    before = dict(notion.databases["db-a"])
    with notion.client() as client:
        add_content_property(client, "db-a")

    (patch,) = [body for method, _, body in notion.calls if method == "PATCH"]
    assert patch == {"properties": {CONTENT_PROPERTY: {"rich_text": {}}}}
    assert {k: v for k, v in notion.databases["db-a"].items() if k != CONTENT_PROPERTY} == before


def test_a_database_that_already_has_it_is_not_changed() -> None:
    notion = Notion({"db-a": {CONTENT_PROPERTY: TEXT}})
    with notion.client() as client:
        assert add_content_property(client, "db-a") is True

    assert notion.asked("PATCH") == []


def test_a_persons_own_column_of_that_name_is_left_as_it_is() -> None:
    """Changing its type to text would empty what the person keeps in it."""
    own = {"type": "select", "select": {"options": [{"name": "초안"}]}}
    notion = Notion({"db-a": {CONTENT_PROPERTY: own}})
    with notion.client() as client:
        assert add_content_property(client, "db-a") is False

    assert notion.asked("PATCH") == []
    assert notion.databases["db-a"][CONTENT_PROPERTY] == own


@pytest.mark.parametrize("status", [400, 403, 404])
def test_a_database_notion_will_not_show_does_not_have_it(status: int) -> None:
    notion = Notion(old_databases())
    notion.get_status["db-a"] = status
    with notion.client() as client:
        assert add_content_property(client, "db-a") is False

    assert notion.asked("PATCH") == []


@pytest.mark.parametrize("status", [400, 403])
def test_a_refused_change_does_not_have_it(status: int) -> None:
    notion = Notion(old_databases())
    notion.patch_status["db-a"] = status
    with notion.client() as client:
        assert add_content_property(client, "db-a") is False

    assert CONTENT_PROPERTY not in notion.databases["db-a"]


@pytest.mark.parametrize("status", [429, 500, 503])
@pytest.mark.parametrize("call", ["GET", "PATCH"])
def test_a_busy_or_broken_notion_is_no_answer(status: int, call: str) -> None:
    notion = Notion(old_databases())
    (notion.get_status if call == "GET" else notion.patch_status)["db-a"] = status
    with notion.client() as client:
        assert add_content_property(client, "db-a") is None


def test_a_notion_that_does_not_answer_is_no_answer_and_does_not_raise() -> None:
    notion = Notion(old_databases())
    notion.down = True
    with notion.client() as client:
        assert add_content_property(client, "db-a") is None


def test_a_proxys_page_instead_of_notions_json_is_no_answer() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, text="<html></html>"))
    with httpx.Client(base_url=notion_setup.NOTION_API, transport=transport) as client:
        assert add_content_property(client, "db-a") is None


# --- a new team: the setup makes the databases with it ---------------------------------


def test_a_new_teams_two_databases_are_made_with_the_property_and_recorded(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    notion = Notion()
    notion.shared = [PAGE]
    monkeypatch.setattr(notion_setup, "notion_client", notion.client)

    notion_connect.set_up(TEAM, PAGE)

    made = {
        body["title"][0]["text"]["content"]: body["properties"]
        for method, path, body in notion.calls
        if method == "POST" and path == "/databases"
    }
    assert made["할 일"][CONTENT_PROPERTY] == {"rich_text": {}}
    assert made["결정"][CONTENT_PROPERTY] == {"rich_text": {}}
    assert CONTENT_PROPERTY not in made["회의록"]
    # Made with it, so nothing is asked about them afterwards.
    assert notion.asked("GET") == [] and notion.asked("PATCH") == []
    config = connection()
    assert property_names(wired, TEAM, config, "action") == ACTION_PROPERTIES
    assert property_names(wired, TEAM, config, "decision") == DECISION_PROPERTIES


# --- a connected team: asked once, at a setup or at its next sync -----------------------


def test_setting_up_again_gives_the_kept_databases_the_property(
    wired: Session, notion: Notion
) -> None:
    connected_before(wired)
    notion.shared = [PAGE]

    notion_connect.set_up(TEAM, PAGE)

    assert notion.asked("PATCH") == ["db-a", "db-d"]
    assert property_names(wired, TEAM, connection(), "action") == ACTION_PROPERTIES
    assert property_names(wired, TEAM, connection(), "decision") == DECISION_PROPERTIES


def test_a_team_that_never_sets_up_again_gets_it_at_its_next_sync(
    wired: Session, notion: Notion, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected_before(wired)
    pages = FakeNotion()
    seen: list[Mapping[str, str] | None] = []
    real = service.sync_action_item_to_notion

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["property_names"])
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "sync_action_item_to_notion", spy)
    monkeypatch.setattr(tasks, "NotionClient", lambda _token: pages)
    row = item(wired)

    assert tasks.sync_action_item(row.id) == tasks.COPY_SENT

    assert notion.asked("PATCH") == ["db-a", "db-d"]
    assert notion.databases["db-a"][CONTENT_PROPERTY] == TEXT
    assert notion.databases["db-d"][CONTENT_PROPERTY] == TEXT
    assert seen == [ACTION_PROPERTIES]
    assert [database for database, _ in pages.pages] == ["db-a"]


def test_the_sync_after_that_asks_notion_nothing(
    wired: Session, notion: Notion, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected_before(wired)
    monkeypatch.setattr(tasks, "NotionClient", lambda _token: FakeNotion())
    row = item(wired)
    tasks.sync_action_item(row.id)
    notion.calls.clear()

    tasks.sync_action_item(row.id)

    assert notion.calls == []


def test_a_refused_change_leaves_the_sync_working_in_todays_shape(
    wired: Session, notion: Notion, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page still goes, under the default names -- which do not name 내용,
    so Notion has nothing to refuse it for."""
    connected_before(wired)
    notion.patch_status = {"db-a": 403, "db-d": 403}
    pages = FakeNotion()
    seen: list[Mapping[str, str] | None] = []
    real = service.sync_action_item_to_notion

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["property_names"])
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "sync_action_item_to_notion", spy)
    monkeypatch.setattr(tasks, "NotionClient", lambda _token: pages)
    row = item(wired)

    assert tasks.sync_action_item(row.id) == tasks.COPY_SENT

    assert seen == [None]
    ((_, properties),) = pages.pages
    assert set(properties) <= set(NOTION_PROPERTIES.values())
    assert (
        properties[NOTION_PROPERTIES["title"]]["title"][0]["text"]["content"] == "릴리스 노트 정리"
    )


def test_a_refusal_is_recorded_so_later_syncs_do_not_ask_again(
    session: Session, notion: Notion
) -> None:
    connected_before(session)
    notion.patch_status = {"db-a": 403, "db-d": 403}
    ensure_content(session, TEAM, connection())
    notion.calls.clear()

    ensure_content(session, TEAM, connection())

    assert notion.calls == []
    assert property_names(session, TEAM, connection(), "action") is None


def test_an_unanswered_question_is_asked_again_at_the_next_sync(
    session: Session, notion: Notion
) -> None:
    connected_before(session)
    notion.down = True
    ensure_content(session, TEAM, connection())
    assert property_names(session, TEAM, connection(), "action") is None

    notion.down = False
    ensure_content(session, TEAM, connection())

    assert property_names(session, TEAM, connection(), "action") == ACTION_PROPERTIES
    assert property_names(session, TEAM, connection(), "decision") == DECISION_PROPERTIES


def test_one_database_unanswered_records_nothing_for_either(
    session: Session, notion: Notion
) -> None:
    """Half an answer kept would stop the other half from ever being asked."""
    connected_before(session)
    notion.get_status["db-d"] = 503

    ensure_content(session, TEAM, connection())

    assert session.get(ExtNotionTarget, TEAM).content_asked_at is None
    assert property_names(session, TEAM, connection(), "action") is None
    # The action database was given it all the same; the next ask finds it there.
    notion.get_status.clear()
    ensure_content(session, TEAM, connection())
    assert notion.asked("PATCH") == ["db-a", "db-d"]
    assert property_names(session, TEAM, connection(), "action") == ACTION_PROPERTIES


def test_each_database_answers_for_itself(session: Session, notion: Notion) -> None:
    """A decision database a person gave their own 내용 column: items get the
    property, decisions keep today's shape."""
    connected_before(session)
    notion.databases["db-d"][CONTENT_PROPERTY] = {"type": "number", "number": {}}

    ensure_content(session, TEAM, connection())

    assert property_names(session, TEAM, connection(), "action") == ACTION_PROPERTIES
    assert property_names(session, TEAM, connection(), "decision") is None


def test_the_decision_sync_asks_too_and_is_given_the_decision_map(
    wired: Session, notion: Notion, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected_before(wired)
    seen: list[Mapping[str, str] | None] = []

    def sync(_session: Session, _notion: Any, **kwargs: Any) -> None:
        seen.append(kwargs["property_names"])

    wired.add(
        ExtDecision(
            id="dec_1",
            meeting_id=MEETING,
            statement="출시는 11월로 미룹니다",
            confidence=0.9,
            origin="model",
        )
    )
    wired.flush()
    monkeypatch.setattr(service, "sync_decision_to_notion", sync)
    monkeypatch.setattr(tasks, "NotionClient", lambda _token: FakeNotion())

    tasks._sync_decision_notion("dec_1")

    assert notion.asked("PATCH") == ["db-a", "db-d"]
    assert seen == [DECISION_PROPERTIES]


def test_the_fill_asks_too(wired: Session, notion: Notion, monkeypatch: pytest.MonkeyPatch) -> None:
    """A setup whose question went unanswered queues the fill all the same; its
    first row asks."""
    connected_before(wired)
    row = item(wired)
    seen: list[Mapping[str, str] | None] = []
    real = service.sync_action_item_to_notion

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["property_names"])
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "sync_action_item_to_notion", spy)
    monkeypatch.setattr(notion_backfill, "NotionClient", lambda _token: FakeNotion())

    notion_backfill._sync_one_action_item(
        row.id, MEETING, notion_backfill.Stats(), notion_backfill._ClientCache()
    )

    assert notion.asked("PATCH") == ["db-a", "db-d"]
    assert seen == [ACTION_PROPERTIES]


def test_the_fill_of_decisions_asks_too_and_is_given_the_decision_map(
    wired: Session, notion: Notion, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected_before(wired)
    seen: list[Mapping[str, str] | None] = []

    def sync(_session: Session, _notion: Any, **kwargs: Any) -> None:
        seen.append(kwargs["property_names"])

    monkeypatch.setattr(service, "sync_decision_to_notion", sync)
    monkeypatch.setattr(notion_backfill, "NotionClient", lambda _token: FakeNotion())

    notion_backfill._sync_one_decision(
        "dec_1", MEETING, notion_backfill.Stats(), notion_backfill._ClientCache()
    )

    assert notion.asked("PATCH") == ["db-a", "db-d"]
    assert seen == [DECISION_PROPERTIES]


# --- who is not asked, and whose map is left alone --------------------------------------


def test_a_team_on_the_local_dev_page_is_not_asked_and_keeps_todays_shape(
    session: Session, notion: Notion
) -> None:
    """Its ids sit in the connection's config; there is no row to keep an answer in."""
    config = connection(action_db_id="db-a", decision_db_id="db-d")

    ensure_content(session, TEAM, config)

    assert notion.calls == []
    assert property_names(session, TEAM, config, "action") is None
    assert property_names(session, TEAM, config, "decision") is None


def test_a_row_from_another_workspace_is_not_asked_about(session: Session, notion: Notion) -> None:
    """Its databases are ones the current token cannot see (#467 review)."""
    connected_before(session)
    elsewhere = IntegrationConfig("notion", TEAM, "ntn_token", {"workspace_id": "ws-2"})

    ensure_content(session, TEAM, elsewhere)

    assert notion.calls == []
    assert property_names(session, TEAM, elsewhere, "action") is None


def test_what_one_workspaces_databases_have_says_nothing_of_anothers(
    session: Session, notion: Notion
) -> None:
    """The team connected another workspace since; the row still says its old
    databases have the property, and the new connection's pages go nowhere
    near them."""
    connected_before(session)
    ensure_content(session, TEAM, connection())
    assert property_names(session, TEAM, connection(), "action") == ACTION_PROPERTIES
    elsewhere = IntegrationConfig("notion", TEAM, "ntn_token", {"workspace_id": "ws-2"})

    assert property_names(session, TEAM, elsewhere, "action") is None
    assert property_names(session, TEAM, elsewhere, "decision") is None


def test_a_team_without_a_token_is_not_asked(session: Session, notion: Notion) -> None:
    connected_before(session)

    ensure_content(session, TEAM, IntegrationConfig("notion", TEAM, None, {"workspace_id": "ws-1"}))

    assert notion.calls == []


def test_a_teams_own_map_is_the_map_whatever_the_record_says(
    session: Session, notion: Notion
) -> None:
    """It replaces the defaults, as it always has: it names ``content`` itself
    or its pages do without."""
    connected_before(session)
    ensure_content(session, TEAM, connection())
    own = {"title": "할 일 이름"}
    named = {"title": "할 일 이름", "content": "본문"}

    assert property_names(session, TEAM, connection(action_properties=own), "action") == own
    assert property_names(session, TEAM, connection(action_properties=named), "action") == named
    # The other database's map is not the item map's business.
    assert (
        property_names(session, TEAM, connection(action_properties=own), "decision")
        == DECISION_PROPERTIES
    )


def test_new_databases_under_another_page_forget_what_the_old_ones_had(
    session: Session, notion: Notion
) -> None:
    """The row's ids change; what was known about the old ids is not theirs."""
    connected_before(session)
    ensure_content(session, TEAM, connection())
    assert property_names(session, TEAM, connection(), "action") == ACTION_PROPERTIES

    connected_before(session, action_db_id="db-new-a", decision_db_id="db-new-d")

    assert property_names(session, TEAM, connection(), "action") is None
    assert session.get(ExtNotionTarget, TEAM).content_asked_at is None


# --- deleting: the map in force reaches the retire ---------------------------------------


class _Pages(FakeNotion):
    """The deleting task closes the client it opened."""

    def close(self) -> None:
        pass


def test_trashing_an_items_page_is_given_the_map_in_force(
    wired: Session, notion: Notion, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retire empties what the map names, so it must be the map the page
    was written with."""
    connected_before(wired)
    pages = _Pages()
    monkeypatch.setattr(tasks, "NotionClient", lambda _token: pages)
    row = item(wired)
    tasks.sync_action_item(row.id)
    seen: list[Mapping[str, str] | None] = []
    monkeypatch.setattr(
        service, "trash_item_page", lambda _client, _page, names: seen.append(names)
    )

    TRASH_NOTION_PAGE(row.id)

    assert seen == [ACTION_PROPERTIES]


def test_a_page_owed_to_the_cleanup_is_retired_with_the_map_in_force_too(
    wired: Session, notion: Notion, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page the deleting request could not trash is trashed by a later run
    (``drain_external_cleanup``); it empties the same properties."""
    connected_before(wired)
    ensure_content(wired, TEAM, connection())
    owed = ExtExternalCleanup(team_id=TEAM, system="notion", external_id="page-1")
    wired.add(owed)
    wired.flush()
    seen: list[Mapping[str, str] | None] = []
    monkeypatch.setattr(
        service, "trash_item_page", lambda _client, _page, names: seen.append(names)
    )
    monkeypatch.setattr(tasks, "NotionClient", lambda _token: _Pages())

    assert tasks._clean_up_one(wired, owed, connection(), None) == 1

    assert seen == [ACTION_PROPERTIES]
