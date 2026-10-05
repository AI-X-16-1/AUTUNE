"""``NotionClient.page_state`` against Notion's answers (#403, #404), over a
mock transport -- no network calls."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autune_integrations.errors import PermanentIntegrationError
from autune_integrations.notion import NotionClient


def client_answering(status: int, body: dict[str, Any] | None = None) -> NotionClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/pages/page_1"
        return httpx.Response(status, json=body or {})

    client = NotionClient("token")
    client._client = httpx.Client(
        base_url="https://api.notion.com/v1", transport=httpx.MockTransport(handler)
    )
    return client


def test_a_404_is_a_deleted_page() -> None:
    assert client_answering(404, {"code": "object_not_found"}).page_state("page_1") == "deleted"


@pytest.mark.parametrize("flag", ["archived", "in_trash"])
def test_an_archived_or_trashed_page_is_archived_not_deleted(flag: str) -> None:
    """A person put it there and can take it back out -- not the same as gone."""
    assert client_answering(200, {"id": "page_1", flag: True}).page_state("page_1") == "archived"


def test_a_live_page_is_live() -> None:
    body = {"id": "page_1", "archived": False, "in_trash": False}
    assert client_answering(200, body).page_state("page_1") == "live"


def test_any_other_refusal_is_raised_not_read_as_a_state() -> None:
    """A revoked token would refuse the new page too."""
    with pytest.raises(PermanentIntegrationError) as caught:
        client_answering(401).page_state("page_1")
    assert caught.value.details["upstream_status"] == 401


def test_trashing_a_page_puts_it_in_notions_trash() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "page_1", "in_trash": True})

    client = NotionClient("token")
    client._client = httpx.Client(
        base_url="https://api.notion.com/v1", transport=httpx.MockTransport(handler)
    )
    assert client.trash_page("page_1") is True
    assert sent == [{"in_trash": True}]


def test_trashing_a_page_that_is_gone_says_so() -> None:
    client = NotionClient("token")
    client._client = httpx.Client(
        base_url="https://api.notion.com/v1",
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={})),
    )
    assert client.trash_page("page_1") is False


def test_trashing_a_page_already_in_the_trash_is_done() -> None:
    """Notion answers an edit to a trashed page with 400; the page is where it
    was asked to go."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(400, json={"code": "validation_error"})
        return httpx.Response(200, json={"id": "page_1", "in_trash": True})

    client = NotionClient("token")
    client._client = httpx.Client(
        base_url="https://api.notion.com/v1", transport=httpx.MockTransport(handler)
    )
    assert client.trash_page("page_1") is True


# --- find_pages: a create that timed out may have arrived (#754 review) ----------------


def test_find_pages_asks_for_this_title_made_since_and_keeps_only_live_pages() -> None:
    from datetime import UTC, datetime

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/databases/db_1/query"
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": "page_live"},
                    {"id": "page_trashed", "in_trash": True},
                    {"id": "page_archived", "archived": True},
                ]
            },
        )

    client = NotionClient("token")
    client._client = httpx.Client(
        base_url="https://api.notion.com/v1", transport=httpx.MockTransport(handler)
    )
    since = datetime(2026, 10, 3, 1, 0, tzinfo=UTC)

    found = client.find_pages(
        "db_1", title_property="작업", title="릴리스 노트 정리", created_after=since
    )

    assert found == ["page_live"]
    (body,) = seen
    assert body["filter"]["and"][0] == {"property": "작업", "title": {"equals": "릴리스 노트 정리"}}
    assert body["filter"]["and"][1] == {
        "timestamp": "created_time",
        "created_time": {"on_or_after": "2026-10-03T01:00:00+00:00"},
    }
    assert body["page_size"] == 10
