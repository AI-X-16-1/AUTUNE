"""``JiraClient.for_cloud`` against Jira Cloud's answers (#82), over a mock
transport -- no network calls."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest

from autune_integrations.jira import JiraClient

CLOUD = "cloud-1"


def client(handler: Callable[[httpx.Request], httpx.Response]) -> JiraClient:
    c = JiraClient.for_cloud("access-token", CLOUD)
    c._client = httpx.Client(
        base_url=f"https://api.atlassian.com/ex/jira/{CLOUD}/rest/api/3",
        headers=c._client.headers,
        transport=httpx.MockTransport(handler),
    )
    return c


def test_for_cloud_addresses_the_site_by_cloud_id_with_the_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"key": "AUT-1"})

    client(handler).create_task("AUT", "스펙 공유")

    assert seen[0].url.path == f"/ex/jira/{CLOUD}/rest/api/3/issue"
    assert seen[0].headers["authorization"] == "Bearer access-token"


def test_an_email_search_is_addressing_not_content() -> None:
    """An email in the query would be refused as personal data if it were
    checked as content."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["query"] == "me@example.com"
        return httpx.Response(
            200,
            json=[
                {"accountId": "app-1", "accountType": "app"},
                {"accountId": "acc-1", "accountType": "atlassian"},
            ],
        )

    assert client(handler).find_account_id("me@example.com") == "acc-1"


def test_nobody_found_is_none() -> None:
    assert client(lambda r: httpx.Response(200, json=[])).find_account_id("x@example.com") is None


def test_a_task_carries_due_date_and_assignee_or_clears_them() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"key": "AUT-1"})

    c = client(handler)
    c.create_task("AUT", "스펙 공유", due_date=date(2026, 10, 7), assignee_account_id="acc-1")
    c.create_task("AUT", "미정 작업")

    first, second = (b["fields"] for b in bodies)
    assert first["duedate"] == "2026-10-07"
    assert first["assignee"] == {"accountId": "acc-1"}
    assert second["duedate"] is None
    assert second["assignee"] is None


def test_updating_an_issue_deleted_in_jira_says_so() -> None:
    c = client(lambda r: httpx.Response(404, json={}))
    assert c.update_task("AUT-1", "x", due_date=None, assignee_account_id=None) is False


def test_moving_to_a_category_uses_whatever_transition_the_workflow_offers() -> None:
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {"id": "11", "to": {"statusCategory": {"key": "new"}}},
                        {"id": "21", "to": {"statusCategory": {"key": "indeterminate"}}},
                        {"id": "31", "to": {"statusCategory": {"key": "done"}}},
                    ]
                },
            )
        posted.append(json.loads(request.content))
        return httpx.Response(204)

    assert client(handler).move_to_category("AUT-1", "done") is True
    assert posted == [{"transition": {"id": "31"}}]


def test_a_workflow_without_that_category_leaves_the_issue_alone() -> None:
    c = client(lambda r: httpx.Response(200, json={"transitions": []}))
    assert c.move_to_category("AUT-1", "done") is False


def test_an_unknown_category_is_a_bug_not_a_request() -> None:
    with pytest.raises(ValueError):
        client(lambda r: httpx.Response(200, json={})).move_to_category("AUT-1", "closed")
