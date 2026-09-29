"""``/dev/connect-notion`` (#342, #402): page-id parsing, the mount gate, and
which databases a connection keeps.

Notion itself is never called: database creation is replaced by a stub.
``notion_setup`` itself is covered in ``test_notion_setup.py``.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from autune_core import get_session
from autune_core.integrations_config import IntegrationConfig
from autune_extraction import notion_setup
from autune_extraction import router as router_module
from autune_extraction.dev import routes
from autune_extraction.dev.page import PAGE as PAGE_HTML
from autune_extraction.dev.routes import ConnectNotion, _parse_page_id

PAGE = "8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"
OTHER_PAGE = "11111111-2222-3333-4444-555555555555"


def test_a_bare_dashed_id_is_kept_whole() -> None:
    """Live bug from review of #342 (lsh2217): the previous parser kept only
    the text after the id's own last dash, turning this into "1234567890ab"."""
    page_id = "8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"

    assert _parse_page_id(page_id) == page_id


def test_a_bare_undashed_id_is_kept_whole() -> None:
    assert _parse_page_id("8e2c9c504b0d4bb0b0b01234567890ab") == "8e2c9c504b0d4bb0b0b01234567890ab"


def test_the_id_is_read_from_a_titled_url() -> None:
    url = "https://www.notion.so/myworkspace/Page-Title-8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"

    assert _parse_page_id(url) == "8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"


def test_a_query_string_is_dropped() -> None:
    url = "https://www.notion.so/myworkspace/8e2c9c50-4b0d-4bb0-b0b0-1234567890ab?pvs=4"

    assert _parse_page_id(url) == "8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"


def test_surrounding_whitespace_is_stripped() -> None:
    assert (
        _parse_page_id("  8e2c9c50-4b0d-4bb0-b0b0-1234567890ab  ")
        == "8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"
    )


def test_a_refused_value_is_not_echoed_back() -> None:
    """mkkim68, review of #402: the token field is right above this one, and a
    token pasted here came back in the 400 body."""
    token = "ntn_secret_value_pasted_into_the_wrong_field"

    with pytest.raises(ValueError) as caught:
        _parse_page_id(token)

    assert token not in str(caught.value)


def test_something_with_no_id_shaped_run_is_refused() -> None:
    with pytest.raises(ValueError, match="page id"):
        _parse_page_id("그냥 아무 텍스트")


# --- the mount gate (lsh2217, review of #402) -------------------------------------


@pytest.mark.parametrize(
    ("env", "opted_in", "mounted"),
    [
        ("local", True, True),
        ("local", False, False),
        ("production", True, False),
        ("staging", True, False),
    ],
)
def test_the_page_needs_local_and_an_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch, env: str, opted_in: bool, mounted: bool
) -> None:
    """``local`` is what every checkout and the demo stack run under, so it
    alone must not serve an unauthenticated route that stores any team's token."""
    monkeypatch.setattr(router_module, "get_core_settings", lambda: SimpleNamespace(env=env))
    monkeypatch.setattr(router_module, "get_settings", lambda: SimpleNamespace(dev_routes=opted_in))

    assert router_module.dev_routes_enabled() is mounted


# --- which databases a connection keeps -------------------------------------------


class _Session:
    def commit(self) -> None:
        pass


def _connect(
    monkeypatch: pytest.MonkeyPatch, stored: dict[str, Any] | None, page_id: str
) -> tuple[dict[str, str], list[str], dict[str, Any]]:
    created: list[str] = []
    saved: dict[str, Any] = {}

    def create(client: httpx.Client, *, title: str, **_: Any) -> str:
        created.append(title)
        return f"db_new_{len(created)}"

    def save(session: Any, team_id: str, service: str, **kw: Any) -> None:
        saved.update(kw)

    existing = (
        IntegrationConfig(service="notion", team_id="team_1", secret="old", config=stored)
        if stored is not None
        else None
    )
    monkeypatch.setattr(routes, "load_integration", lambda *a: existing)
    monkeypatch.setattr(routes, "save_integration", save)
    monkeypatch.setattr(notion_setup, "create_database", create)

    body = ConnectNotion(team_id="team_1", token="new", page_id=page_id)
    result = routes.connect_notion(body, _Session())  # type: ignore[arg-type]
    return result, created, saved


def test_connecting_the_same_page_again_keeps_its_databases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = {
        "action_db_id": "db_a",
        "decision_db_id": "db_d",
        "minutes_db_id": "db_m",
        "parent_page_id": PAGE,
    }

    result, created, saved = _connect(monkeypatch, stored, PAGE.replace("-", ""))

    assert created == []
    assert (result["action_db_id"], result["decision_db_id"]) == ("db_a", "db_d")
    assert result["minutes_db_id"] == "db_m"
    assert result["databases"] == "reused"
    assert result["action_db_url"] == "https://www.notion.so/db_a"
    assert saved["secret"] == "new"


def test_a_connection_from_before_minutes_existed_gets_only_that_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team connected before #428 has two ids under its page; reconnecting the
    same page adds the 회의록 database and keeps the two it already writes to."""
    stored = {"action_db_id": "db_a", "decision_db_id": "db_d", "parent_page_id": PAGE}

    result, created, saved = _connect(monkeypatch, stored, PAGE)

    assert created == ["회의록"]
    assert result["databases"] == "added"
    assert saved["config"] == {
        "action_db_id": "db_a",
        "decision_db_id": "db_d",
        "minutes_db_id": "db_new_1",
        "parent_page_id": PAGE,
    }


def test_a_different_page_gets_its_own_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    """lsh2217, review of #402: reconnecting with another page kept the old
    database ids, which the new token may not be able to reach, so every later
    sync failed."""
    stored = {"action_db_id": "db_a", "decision_db_id": "db_d", "parent_page_id": PAGE}

    result, created, saved = _connect(monkeypatch, stored, OTHER_PAGE)

    assert created == ["액션 아이템", "결정", "회의록"]
    assert result["action_db_id"] == "db_new_1"
    assert result["databases"] == "created"
    assert saved["config"]["parent_page_id"] == OTHER_PAGE


def test_a_connection_stored_without_its_page_gets_new_databases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows saved before the page was recorded cannot be matched to one."""
    stored = {"action_db_id": "db_a", "decision_db_id": "db_d"}

    _result, created, _saved = _connect(monkeypatch, stored, PAGE)

    assert len(created) == 3


def test_notions_refusal_reaches_the_page_with_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Notion's own message names what was wrong (page not shared, bad id) --
    what the person filling in the form needs to read."""

    def refuse(client: httpx.Client, **_: Any) -> str:
        raise notion_setup.NotionSetupError(404, "Could not find page with ID")

    monkeypatch.setattr(routes, "load_integration", lambda *a: None)
    monkeypatch.setattr(notion_setup, "create_database", refuse)

    body = ConnectNotion(team_id="team_1", token="new", page_id=PAGE)
    with pytest.raises(HTTPException) as caught:
        routes.connect_notion(body, _Session())  # type: ignore[arg-type]

    assert caught.value.status_code == 404
    assert caught.value.detail == "Could not find page with ID"


# --- what the page sends (PARKJAEKYUNG0525, review of #402) -----------------------


def _page_fields() -> set[str]:
    """The keys ``connectNotion()`` in the page puts in its JSON body."""
    body = PAGE_HTML.split("function connectNotion()", 1)[1].split("}, ", 1)[0]
    return set(re.findall(r"^\s+(\w+): document\.getElementById", body, re.M))


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(routes, "load_integration", lambda *a: None)
    monkeypatch.setattr(routes, "save_integration", lambda *a, **k: None)
    monkeypatch.setattr(
        notion_setup, "create_database", lambda client, *, title, **_: f"db_{title}"
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/dev")
    app.dependency_overrides[get_session] = lambda: _Session()
    return TestClient(app)


def test_the_page_sends_exactly_the_fields_the_route_reads() -> None:
    """The page sent action_db_id/decision_db_id after the route had moved to
    page_id, so every connect from it was a 422."""
    assert _page_fields() == set(ConnectNotion.model_fields)


def test_the_pages_payload_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = dict.fromkeys(_page_fields(), "x") | {"page_id": PAGE, "token": "ntn_token"}

    response = _client(monkeypatch).post("/dev/connect-notion", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "connected"


def test_a_refused_request_does_not_send_the_token_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """FastAPI's default 422 put the whole body, token included, in ``input``."""
    token = "ntn_secret_value"

    response = _client(monkeypatch).post(
        "/dev/connect-notion", json={"team_id": "team_1", "token": token}
    )

    assert response.status_code == 422
    assert token not in response.text
    assert response.json()["detail"] == [{"loc": ["page_id"], "msg": "Field required"}]
