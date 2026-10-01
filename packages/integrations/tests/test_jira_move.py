"""``JiraClient.move_to_category`` leaves a status the team chose (#458 review).
Mock transport, no network."""

from __future__ import annotations

from typing import Any

import httpx

from autune_integrations.jira import JiraClient


def _client(current: str, posted: list[dict[str, Any]]) -> JiraClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/transitions"):
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {"id": "21", "to": {"statusCategory": {"key": "indeterminate"}}},
                        {"id": "31", "to": {"statusCategory": {"key": "done"}}},
                    ]
                },
            )
        if request.method == "GET":
            return httpx.Response(
                200, json={"fields": {"status": {"statusCategory": {"key": current}}}}
            )
        posted.append({"path": request.url.path, "body": request.content.decode()})
        return httpx.Response(204)

    client = JiraClient.for_cloud("token", "cloud-1")
    client._client = httpx.Client(
        base_url="https://api.atlassian.com/ex/jira/cloud-1/rest/api/3",
        transport=httpx.MockTransport(handler),
    )
    return client


def test_an_issue_in_review_is_not_moved_to_in_progress() -> None:
    posted: list[dict[str, Any]] = []
    assert _client("indeterminate", posted).move_to_category("KAN-4", "indeterminate") is True
    assert posted == []


def test_an_issue_in_another_category_is_moved() -> None:
    posted: list[dict[str, Any]] = []
    assert _client("indeterminate", posted).move_to_category("KAN-4", "done") is True
    assert len(posted) == 1 and '"31"' in posted[0]["body"]
