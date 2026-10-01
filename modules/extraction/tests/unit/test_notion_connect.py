"""After a one-click Notion connection (#428): the pages to choose from, where
the database ids are read from, and setting up then filling."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base
from autune_core.integrations_config import IntegrationConfig
from autune_extraction import notion_connect, notion_setup
from autune_extraction.models import ExtNotionTarget

TEAM = "team_1"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[ExtNotionTarget.__table__])
    with Session(engine) as s:
        yield s


def _page(page_id: str, title: str, *, parent: str = "workspace", archived: bool = False) -> dict:
    return {
        "id": page_id,
        "archived": archived,
        "parent": {"type": parent},
        "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}},
    }


def test_shared_pages_leave_out_database_rows_and_archived_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    _page("p1", "팀 위키"),
                    _page("p2", "이전 액션", parent="database_id"),
                    _page("p3", "버린 페이지", archived=True),
                    _page("p4", "", parent="page_id"),
                ]
            },
        )

    client = httpx.Client(base_url=notion_setup.NOTION_API, transport=httpx.MockTransport(handler))
    assert notion_setup.shared_pages(client) == [
        {"id": "p1", "title": "팀 위키"},
        {"id": "p4", "title": "(제목 없음)"},
    ]


TARGET = {
    "parent_page_id": "page-1",
    "action_db_id": "db-a",
    "decision_db_id": "db-d",
    "minutes_db_id": "db-m",
}


def test_bs_own_table_is_read_before_the_connection_config(session: Session) -> None:
    dev_config = IntegrationConfig(
        "notion", TEAM, "t", {"action_db_id": "old-a", "workspace_id": "ws-1"}
    )
    assert notion_setup.database_id(session, TEAM, dev_config, "action_db_id") == "old-a"

    notion_setup.save_targets(session, TEAM, TARGET, workspace_id="ws-1")

    assert notion_setup.database_id(session, TEAM, dev_config, "action_db_id") == "db-a"
    assert notion_setup.stored_targets(session, TEAM, dev_config) == TARGET


def test_databases_from_another_workspace_are_not_used(session: Session) -> None:
    """#467 review: disconnect, connect another workspace -- the old ids are
    databases the new token cannot see, and every sync would be refused."""
    notion_setup.save_targets(session, TEAM, TARGET, workspace_id="ws-old")
    now = IntegrationConfig("notion", TEAM, "t", {"workspace_id": "ws-new"})

    assert notion_setup.database_id(session, TEAM, now, "action_db_id") is None
    assert notion_setup.stored_targets(session, TEAM, now) == {}


def _pages_for(
    session: Session, monkeypatch: pytest.MonkeyPatch, pages: list[dict] | Exception
) -> dict[str, Any]:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session

    class _Client:
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *exc: Any) -> None:
            pass

    def shared(client: Any) -> list[dict]:
        if isinstance(pages, Exception):
            raise pages
        return pages

    monkeypatch.setattr(notion_connect, "session_scope", scope)
    monkeypatch.setattr(
        notion_connect,
        "load_integration",
        lambda _s, team, svc: IntegrationConfig("notion", team, "t", {"workspace_id": "ws-1"}),
    )
    monkeypatch.setattr(notion_setup, "notion_client", lambda token: _Client())
    monkeypatch.setattr(notion_setup, "shared_pages", shared)
    return notion_connect.pages_for(TEAM)


def test_a_parent_page_still_shared_keeps_its_target(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    notion_setup.save_targets(session, TEAM, TARGET, workspace_id="ws-1")
    answer = _pages_for(session, monkeypatch, [{"id": "page-1", "title": "팀 위키"}])
    assert answer["target"]["parent_page_id"] == "page-1"


def test_a_parent_page_no_longer_shared_asks_for_a_page_again(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    notion_setup.save_targets(session, TEAM, TARGET, workspace_id="ws-1")
    answer = _pages_for(session, monkeypatch, [{"id": "page-9", "title": "다른 페이지"}])
    assert answer["target"] is None
    assert answer["pages"] == [{"id": "page-9", "title": "다른 페이지"}]


def test_a_refused_token_asks_for_a_reconnect_not_a_500(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = _pages_for(session, monkeypatch, notion_setup.NotionSetupError(401, "unauthorized"))
    assert answer["needs_reconnect"] is True
    assert answer["target"] is None


def test_notion_being_down_is_still_an_error(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(notion_setup.NotionSetupError):
        _pages_for(session, monkeypatch, notion_setup.NotionSetupError(502, "bad gateway"))


def test_setting_up_records_the_databases_then_fills_them(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    calls: list[str] = []

    class _Client:
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *exc: Any) -> None:
            pass

    def provision(client: Any, *, page_id: str, stored: dict) -> tuple[dict, list[str]]:
        calls.append(f"provision:{page_id}:{bool(stored)}")
        return {**TARGET, "parent_page_id": page_id}, [
            "action_db_id",
            "decision_db_id",
            "minutes_db_id",
        ]

    def items(rows: list, stats: Any) -> None:
        calls.append("items")
        stats.sent += 2
        stats.replaced += 1

    def decisions(rows: list, stats: Any) -> None:
        calls.append("decisions")
        stats.sent += 1

    monkeypatch.setattr(notion_connect, "session_scope", scope)
    monkeypatch.setattr(
        notion_connect,
        "load_integration",
        lambda _s, team, svc: IntegrationConfig(
            "notion", team, "ntn_token", {"workspace_id": "ws-1"}
        ),
    )
    monkeypatch.setattr(notion_setup, "notion_client", lambda token: _Client())
    monkeypatch.setattr(notion_setup, "provision_databases", provision)
    monkeypatch.setattr(
        notion_connect.notion_backfill, "_confirmed_action_items", lambda t: [("a", "m")]
    )
    monkeypatch.setattr(
        notion_connect.notion_backfill, "_confirmed_decisions", lambda t: [("d", "m")]
    )
    monkeypatch.setattr(notion_connect.notion_backfill, "backfill_action_items", items)
    monkeypatch.setattr(notion_connect.notion_backfill, "backfill_decisions", decisions)

    result = notion_connect.set_up(TEAM, "page-2")

    assert calls == ["provision:page-2:False", "items", "decisions"]  # recorded before filling
    row = session.get(ExtNotionTarget, TEAM)
    assert row is not None and row.parent_page_id == "page-2"
    assert row.workspace_id == "ws-1"  # the workspace it was made in
    assert result["databases"] == "created"
    assert (result["action_items"]["sent"], result["action_items"]["replaced"]) == (2, 1)
    assert result["decisions"]["sent"] == 1


def test_setting_up_without_a_connection_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def scope() -> Iterator[None]:
        yield None

    monkeypatch.setattr(notion_connect, "session_scope", scope)
    monkeypatch.setattr(notion_connect, "load_integration", lambda _s, team, svc: None)

    with pytest.raises(notion_setup.NotionSetupError) as caught:
        notion_connect.set_up(TEAM, "page-2")
    assert caught.value.status_code == 409
