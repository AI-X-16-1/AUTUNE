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
    dev_config = IntegrationConfig("notion", TEAM, "t", {"action_db_id": "old-a"})
    assert notion_setup.database_id(session, TEAM, dev_config, "action_db_id") == "old-a"

    notion_setup.save_targets(session, TEAM, TARGET)

    assert notion_setup.database_id(session, TEAM, dev_config, "action_db_id") == "db-a"
    assert notion_setup.stored_targets(session, TEAM) == TARGET


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
        lambda _s, team, svc: IntegrationConfig("notion", team, "ntn_token", {}),
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
    assert session.get(ExtNotionTarget, TEAM).parent_page_id == "page-2"  # type: ignore[union-attr]
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
