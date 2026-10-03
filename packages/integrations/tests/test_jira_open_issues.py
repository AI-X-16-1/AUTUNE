"""``JiraClient.open_issues``: a project's unfinished issues, read to be shown
(2026-10-02). Over a mock transport -- no network calls."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx
import pytest

from autune_integrations import PermanentIntegrationError
from autune_integrations.jira import JiraClient, JiraIssue

CLOUD = "cloud-1"


def client(handler: Callable[[httpx.Request], httpx.Response]) -> JiraClient:
    c = JiraClient.for_cloud("access-token", CLOUD)
    c._client = httpx.Client(
        base_url=f"https://api.atlassian.com/ex/jira/{CLOUD}/rest/api/3",
        headers=c._client.headers,
        transport=httpx.MockTransport(handler),
    )
    return c


def test_it_asks_for_one_projects_unfinished_issues_and_four_fields() -> None:
    """The query is bounded to the project, leaves out what is done and any
    issue with a security level (the list shows to the whole team), and asks
    for the fields a row shows -- no description, no comments."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"issues": [], "isLast": True})

    assert client(handler).open_issues("AUT") == ([], False)

    assert seen[0].method == "GET"
    assert seen[0].url.path == f"/ex/jira/{CLOUD}/rest/api/3/search/jql"
    params = seen[0].url.params
    assert params["jql"] == (
        'project = "AUT" AND level is EMPTY AND statusCategory != Done ORDER BY updated DESC'
    )
    assert params["fields"] == "summary,status,assignee,duedate"
    assert params["maxResults"] == "50"


def test_an_issue_is_read_as_the_row_a_list_shows() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "issues": [
                    {
                        "key": "AUT-7",
                        "fields": {
                            "summary": "배포 일정 공유",
                            "status": {
                                "name": "진행 중",
                                "statusCategory": {"key": "indeterminate"},
                            },
                            "assignee": {"displayName": "가나다", "emailAddress": "x@example.com"},
                            "duedate": "2026-10-09",
                        },
                    },
                    # Nobody assigned, no date, a status with no category.
                    {
                        "key": "AUT-8",
                        "fields": {"summary": "미정", "status": {"name": "Backlog"}},
                    },
                    # Not an issue this list can name.
                    {"fields": {"summary": "키 없음"}},
                ],
                "isLast": True,
            },
        )

    issues, more = client(handler).open_issues("AUT")

    assert issues == [
        JiraIssue(
            "AUT-7", "배포 일정 공유", "진행 중", "indeterminate", "가나다", date(2026, 10, 9)
        ),
        JiraIssue("AUT-8", "미정", "Backlog", None, None, None),
    ]
    assert more is False


@pytest.mark.parametrize(
    "answer",
    [
        {"issues": [], "nextPageToken": "abc"},
        {"issues": [], "isLast": False},
    ],
)
def test_it_says_when_jira_has_more_than_it_read(answer: dict[str, object]) -> None:
    assert client(lambda _: httpx.Response(200, json=answer)).open_issues("AUT") == ([], True)


def test_the_page_is_never_larger_than_jira_gives_with_fields() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"issues": []})

    client(handler).open_issues("AUT", limit=5000)

    assert seen[0].url.params["maxResults"] == "100"


@pytest.mark.parametrize("key", ['AUT" OR project = "HR', "", "AUT 1", "7UP", 'A"'])
def test_a_key_that_could_end_the_query_string_is_refused_before_any_request(key: str) -> None:
    """The key is written inside quotes in the JQL. One carrying a quote would
    read another project with the team's token."""

    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be made")

    with pytest.raises(PermanentIntegrationError):
        client(handler).open_issues(key)


def test_a_date_jira_did_not_write_as_a_date_is_no_date() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"issues": [{"key": "AUT-1", "fields": {"summary": "x", "duedate": "soon"}}]},
        )

    issues, _ = client(handler).open_issues("AUT")

    assert issues[0].due_date is None
