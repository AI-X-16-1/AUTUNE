"""``notion_setup`` (#428): the databases a Notion connection gets, and what
the setup calls send. Notion is replaced by a mock transport."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autune_extraction import notion_setup
from autune_extraction.notion_setup import (
    DATABASES,
    HOME_INTRO,
    HOME_TITLE,
    MINUTES_NOTION_PROPERTIES,
    NotionSetupError,
    create_database,
    provision_databases,
    schema,
)
from autune_extraction.service import DECISION_NOTION_PROPERTIES, NOTION_PROPERTIES

PAGE = "8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"


def _recording_client(bodies: list[dict[str, Any]]) -> httpx.Client:
    """Records each POST body with its path under ``_path``. The "Autune" page
    is ``home``; databases are ``db_1``... in creation order; a database
    asked about sits in ``home_kept``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"parent": {"type": "page_id", "page_id": "home_kept"}})
        path = request.url.path.removeprefix("/v1")
        bodies.append(json.loads(request.content) | {"_path": path})
        if path == "/pages":
            return httpx.Response(200, json={"id": "home"})
        made = sum(1 for b in bodies if b["_path"] == "/databases")
        return httpx.Response(200, json={"id": f"db_{made}"})

    return httpx.Client(base_url=notion_setup.NOTION_API, transport=httpx.MockTransport(handler))


def _databases(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for b in bodies if b["_path"] == "/databases"]


def test_a_new_connection_gets_an_autune_page_with_three_databases_in_it() -> None:
    """Decided with the user (2026-10-01): one "Autune" page under the page
    the team shared, and every database inside it."""
    bodies: list[dict[str, Any]] = []
    with _recording_client(bodies) as client:
        config, created = provision_databases(client, page_id=PAGE, stored={})

    home, *databases = bodies
    assert home["_path"] == "/pages"
    assert home["parent"] == {"page_id": PAGE}
    assert home["properties"]["title"]["title"][0]["text"]["content"] == "Autune"
    assert [b["title"][0]["text"]["content"] for b in databases] == [
        "할 일",
        "결정",
        "회의록",
    ]
    assert all(b["parent"] == {"page_id": "home"} for b in databases)
    assert config == {
        "action_db_id": "db_1",
        "decision_db_id": "db_2",
        "minutes_db_id": "db_3",
        "parent_page_id": PAGE,
    }
    assert created == ["action_db_id", "decision_db_id", "minutes_db_id"]


def test_each_database_has_exactly_the_columns_its_sync_writes() -> None:
    """The schema is built from the same name maps the sync uses, so the two
    cannot drift apart. The item and decision databases also get 내용, which the
    sync names only for a database known to have it
    (``test_notion_content_property``)."""
    assert [names for _, _, names, _ in DATABASES] == [
        {**NOTION_PROPERTIES, "content": "내용"},
        {**DECISION_NOTION_PROPERTIES, "content": "내용"},
        MINUTES_NOTION_PROPERTIES,
    ]
    for _, _, names, status_select in DATABASES:
        assert set(schema(names, status_select=status_select)) == set(names.values())


def test_the_minutes_database_dates_its_meetings() -> None:
    properties = schema(MINUTES_NOTION_PROPERTIES, status_select=False)

    assert properties["제목"] == {"title": {}}
    assert properties["날짜"] == {"date": {}}
    assert properties["회의"] == {"rich_text": {}}


def test_nothing_but_titles_and_property_names_leaves() -> None:
    """No meeting content goes out at setup: what is sent is fixed at import."""
    bodies: list[dict[str, Any]] = []
    with _recording_client(bodies) as client:
        provision_databases(client, page_id=PAGE, stored={})

    home, *databases = bodies
    assert set(home) == {"_path", "parent", "properties", "children"}
    assert home["properties"]["title"]["title"][0]["text"]["content"] == HOME_TITLE
    assert home["children"][0]["paragraph"]["rich_text"][0]["text"]["content"] == HOME_INTRO
    assert all(set(b) == {"_path", "parent", "title", "properties"} for b in databases)


def test_a_missing_database_is_added_beside_the_ones_kept() -> None:
    """A team set up before the 회의록 database, or before the "Autune" page,
    gets the one it lacks where its others are -- and no second home page."""
    stored = {"action_db_id": "db_a", "decision_db_id": "db_d", "parent_page_id": PAGE}
    bodies: list[dict[str, Any]] = []
    with _recording_client(bodies) as client:
        config, created = provision_databases(client, page_id=PAGE, stored=stored)

    assert [b["_path"] for b in bodies] == ["/databases"]
    assert bodies[0]["parent"] == {"page_id": "home_kept"}
    assert created == ["minutes_db_id"]
    assert (config["action_db_id"], config["minutes_db_id"]) == ("db_a", "db_1")


def test_with_no_page_the_autune_page_goes_at_the_top_of_the_workspace() -> None:
    bodies: list[dict[str, Any]] = []
    with _recording_client(bodies) as client:
        notion_setup.create_home_page(client, page_id=None)

    assert bodies[0]["parent"] == {"workspace": True}


def test_a_home_page_given_holds_the_databases_itself() -> None:
    """``set_up`` with nothing shared made the "Autune" page already."""
    bodies: list[dict[str, Any]] = []
    with _recording_client(bodies) as client:
        config, _ = provision_databases(client, page_id="home-1", stored={}, home="home-1")

    assert [b["_path"] for b in bodies] == ["/databases"] * 3
    assert all(b["parent"] == {"page_id": "home-1"} for b in bodies)
    assert config["parent_page_id"] == "home-1"


def _notion(
    calls: list[tuple[str, str, dict[str, Any] | None]],
    *,
    database: httpx.Response,
) -> httpx.Client:
    """Answers ``GET /databases/<id>`` with ``database``; records every call."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, path, body))
        if request.method == "GET":
            return database
        if path == "/pages":
            return httpx.Response(200, json={"id": "home"})
        return httpx.Response(200, json={"id": "db_new"})

    return httpx.Client(base_url=notion_setup.NOTION_API, transport=httpx.MockTransport(handler))


def test_a_kept_database_loses_the_status_codes_it_gained_before_622() -> None:
    """mkkim68, review of #622: a database set up before it had four codes and
    four labels. The codes go; the fill every setup queues relabels the pages."""
    options = [
        {"id": "o1", "name": "todo"},
        {"id": "o2", "name": "done"},
        {"id": "o3", "name": "진행 전"},
        {"id": "o4", "name": "완료"},
    ]
    database = httpx.Response(
        200,
        json={
            "parent": {"type": "page_id", "page_id": "home_kept"},
            "properties": {"상태": {"select": {"options": options}}},
        },
    )
    stored = {
        "action_db_id": "db_a",
        "decision_db_id": "db_d",
        "minutes_db_id": "db_m",
        "parent_page_id": PAGE,
    }
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    with _notion(calls, database=database) as client:
        provision_databases(client, page_id=PAGE, stored=stored)

    (patch,) = [c for c in calls if c[0] == "PATCH"]
    assert patch[1] == "/databases/db_a"
    assert patch[2] == {
        "properties": {
            "상태": {
                "select": {
                    "options": [{"id": "o3", "name": "진행 전"}, {"id": "o4", "name": "완료"}]
                }
            }
        }
    }


def test_a_database_with_only_labels_is_not_touched() -> None:
    database = httpx.Response(
        200, json={"properties": {"상태": {"select": {"options": [{"id": "o", "name": "완료"}]}}}}
    )
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    with _notion(calls, database=database) as client:
        notion_setup.retire_status_codes(client, "db_a")

    assert [c[0] for c in calls] == ["GET"]


def test_a_kept_database_notion_no_longer_has_does_not_fail_the_setup() -> None:
    """mkkim68, review of #622: a 404 there failed the whole setup. The missing
    database goes in a new "Autune" page instead."""
    stored = {"action_db_id": "db_a", "decision_db_id": "db_d", "parent_page_id": PAGE}
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    with _notion(calls, database=httpx.Response(404, json={"message": "gone"})) as client:
        config, created = provision_databases(client, page_id=PAGE, stored=stored)

    posts = [(path, body) for method, path, body in calls if method == "POST"]
    assert [path for path, _ in posts] == ["/pages", "/databases"]
    assert posts[1][1]["parent"] == {"page_id": "home"}
    assert created == ["minutes_db_id"]


def test_the_status_options_are_the_boards_column_names() -> None:
    """The codes went out as they were and were hard to tell apart in Notion."""
    properties = schema(NOTION_PROPERTIES, status_select=True)

    options = [o["name"] for o in properties[NOTION_PROPERTIES["status"]]["select"]["options"]]
    assert options == ["확인 필요", "진행 전", "진행 중", "완료"]


def test_a_stored_database_under_the_same_page_is_kept() -> None:
    stored = {
        "action_db_id": "db_a",
        "decision_db_id": "db_d",
        "minutes_db_id": "db_m",
        "parent_page_id": PAGE.replace("-", ""),
    }
    bodies: list[dict[str, Any]] = []
    with _recording_client(bodies) as client:
        config, created = provision_databases(client, page_id=PAGE, stored=stored)

    assert bodies == []
    assert created == []
    assert config["minutes_db_id"] == "db_m"


def test_a_non_json_refusal_from_notion_is_reported_not_a_500() -> None:
    """lsh2217, review of #402: a proxy's HTML page made ``resp.json()`` raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    transport = httpx.MockTransport(handler)
    with (
        httpx.Client(base_url=notion_setup.NOTION_API, transport=transport) as client,
        pytest.raises(NotionSetupError) as caught,
    ):
        create_database(
            client, page_id=PAGE, title="결정", names={"title": "결정"}, status_select=False
        )

    assert caught.value.status_code == 502
    assert caught.value.message == "Notion answered 502"


def test_notions_own_message_is_kept() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Could not find page with ID"})

    transport = httpx.MockTransport(handler)
    with (
        httpx.Client(base_url=notion_setup.NOTION_API, transport=transport) as client,
        pytest.raises(NotionSetupError) as caught,
    ):
        create_database(
            client, page_id=PAGE, title="결정", names={"title": "결정"}, status_select=False
        )

    assert (caught.value.status_code, caught.value.message) == (404, "Could not find page with ID")
