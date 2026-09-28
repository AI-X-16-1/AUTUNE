"""``NotionClient.page_is_gone`` against Notion's answers (#403), over a mock
transport -- no network calls."""

from __future__ import annotations

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


def test_a_deleted_page_is_gone() -> None:
    assert client_answering(404, {"code": "object_not_found"}).page_is_gone("page_1")


@pytest.mark.parametrize("flag", ["archived", "in_trash"])
def test_an_archived_or_trashed_page_is_gone(flag: str) -> None:
    assert client_answering(200, {"id": "page_1", flag: True}).page_is_gone("page_1")


def test_a_live_page_is_not_gone() -> None:
    body = {"id": "page_1", "archived": False, "in_trash": False}
    assert not client_answering(200, body).page_is_gone("page_1")


def test_any_other_refusal_is_raised_not_read_as_gone() -> None:
    """A revoked token would refuse the new page too, so it is not "gone"."""
    with pytest.raises(PermanentIntegrationError) as caught:
        client_answering(401).page_is_gone("page_1")
    assert caught.value.details["upstream_status"] == 401
