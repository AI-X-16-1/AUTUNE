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
    MINUTES_NOTION_PROPERTIES,
    NotionSetupError,
    create_database,
    provision_databases,
    schema,
)
from autune_extraction.service import DECISION_NOTION_PROPERTIES, NOTION_PROPERTIES

PAGE = "8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"


def _recording_client(bodies: list[dict[str, Any]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": f"db_{len(bodies)}"})

    return httpx.Client(base_url=notion_setup.NOTION_API, transport=httpx.MockTransport(handler))


def test_a_new_connection_gets_three_databases_under_its_page() -> None:
    bodies: list[dict[str, Any]] = []
    with _recording_client(bodies) as client:
        config, created = provision_databases(client, page_id=PAGE, stored={})

    assert [b["title"][0]["text"]["content"] for b in bodies] == ["액션 아이템", "결정", "회의록"]
    assert all(b["parent"] == {"page_id": PAGE} for b in bodies)
    assert config == {
        "action_db_id": "db_1",
        "decision_db_id": "db_2",
        "minutes_db_id": "db_3",
        "parent_page_id": PAGE,
    }
    assert created == ["action_db_id", "decision_db_id", "minutes_db_id"]


def test_each_database_has_exactly_the_columns_its_sync_writes() -> None:
    """The schema is built from the same name maps the sync uses, so the two
    cannot drift apart."""
    assert [names for _, _, names, _ in DATABASES] == [
        NOTION_PROPERTIES,
        DECISION_NOTION_PROPERTIES,
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

    assert all(set(b) == {"parent", "title", "properties"} for b in bodies)


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
