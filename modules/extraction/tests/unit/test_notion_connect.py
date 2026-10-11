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
                    _page("p2", "이전 할 일", parent="database_id"),
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


def _set_up(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    page_id: str | None = "page-2",
    shared: tuple[str, ...] = (),
) -> dict[str, Any]:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()
        calls.append("commit")

    class _Client:
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *exc: Any) -> None:
            pass

    def provision(
        client: Any, *, page_id: str, stored: dict, home: str | None = None
    ) -> tuple[dict, list[str]]:
        calls.append(f"provision:{page_id}:{bool(stored)}" + (f":home={home}" if home else ""))
        return {**TARGET, "parent_page_id": page_id}, [
            "action_db_id",
            "decision_db_id",
            "minutes_db_id",
        ]

    monkeypatch.setattr(notion_connect, "session_scope", scope)
    monkeypatch.setattr(
        notion_connect,
        "load_integration",
        lambda _s, team, svc: IntegrationConfig(
            "notion", team, "ntn_token", {"workspace_id": "ws-1"}
        ),
    )
    monkeypatch.setattr(notion_setup, "lock_setup", lambda _s, team: calls.append(f"lock:{team}"))
    monkeypatch.setattr(notion_setup, "notion_client", lambda token: _Client())
    monkeypatch.setattr(notion_setup, "provision_databases", provision)
    monkeypatch.setattr(
        notion_setup, "shared_pages", lambda client: [{"id": p, "title": p} for p in shared]
    )

    def home_page(client: Any, *, page_id: str | None) -> str:
        calls.append(f"home:{page_id}")
        return "home-1"

    monkeypatch.setattr(notion_setup, "create_home_page", home_page)
    monkeypatch.setattr(
        notion_connect.tasks.backfill_notion, "delay", lambda team: calls.append(f"queued:{team}")
    )
    return notion_connect.set_up(TEAM, page_id)


def test_setting_up_records_the_databases_then_queues_the_fill(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    result = _set_up(session, monkeypatch, calls)

    # Locked before anything is read; queued only once the row is committed,
    # or the worker could look for databases not yet recorded.
    assert calls == ["lock:team_1", "provision:page-2:False", "commit", "queued:team_1"]
    row = session.get(ExtNotionTarget, TEAM)
    assert row is not None and row.parent_page_id == "page-2"
    assert row.workspace_id == "ws-1"  # the workspace it was made in
    assert result["databases"] == "created"
    assert result["backfill"] == "queued"
    assert "action_items" not in result  # nothing was sent inside the request (#481)


def test_with_no_page_shared_an_autune_page_is_made_in_the_workspace(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decided with the user (2026-10-01): allowing access without picking a
    page still sets Autune up, among the person's private pages."""
    calls: list[str] = []

    _set_up(session, monkeypatch, calls, page_id=None)

    assert calls[:3] == ["lock:team_1", "home:None", "provision:home-1:False:home=home-1"]
    row = session.get(ExtNotionTarget, TEAM)
    assert row is not None and row.parent_page_id == "home-1"


def test_with_no_page_a_setup_still_shared_is_kept(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening the screen again must not make a second "Autune" page."""
    session.add(ExtNotionTarget(team_id=TEAM, workspace_id="ws-1", **TARGET))
    session.commit()
    calls: list[str] = []

    _set_up(session, monkeypatch, calls, page_id=None, shared=(TARGET["parent_page_id"],))

    assert not any(c.startswith("home:") for c in calls)
    assert f"provision:{TARGET['parent_page_id']}:True" in calls


def test_the_lock_is_a_no_op_on_sqlite(session: Session) -> None:
    notion_setup.lock_setup(session, TEAM)  # SQLite has no advisory locks; must not raise


def test_the_fill_sends_the_teams_confirmed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def items(rows: list, stats: Any) -> None:
        seen["items"] = rows
        stats.sent += 2

    def decisions(rows: list, stats: Any) -> None:
        seen["decisions"] = rows
        stats.sent += 1

    backfill = notion_connect.tasks.notion_backfill
    monkeypatch.setattr(backfill, "_confirmed_action_items", lambda t: [("a", f"m-{t}")])
    monkeypatch.setattr(backfill, "_confirmed_decisions", lambda t: [("d", f"m-{t}")])
    # #669: the pages of decisions no longer confirmed go through the same
    # call, after the confirmed ones.
    monkeypatch.setattr(backfill, "_decision_pages_to_retire", lambda t: [("gone", f"m-{t}")])
    monkeypatch.setattr(backfill, "backfill_action_items", items)
    monkeypatch.setattr(backfill, "backfill_decisions", decisions)

    notion_connect.tasks.backfill_notion(TEAM)

    assert seen == {
        "items": [("a", "m-team_1")],
        "decisions": [("d", "m-team_1"), ("gone", "m-team_1")],
    }


def test_setting_up_without_a_connection_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def scope() -> Iterator[None]:
        yield None

    monkeypatch.setattr(notion_connect, "session_scope", scope)
    monkeypatch.setattr(notion_setup, "lock_setup", lambda _s, team: None)
    monkeypatch.setattr(notion_connect, "load_integration", lambda _s, team, svc: None)

    with pytest.raises(notion_setup.NotionSetupError) as caught:
        notion_connect.set_up(TEAM, "page-2")
    assert caught.value.status_code == 409
