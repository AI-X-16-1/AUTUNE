"""Jira Cloud.

Two ways in. ``JiraClient.for_cloud`` is the product's: an OAuth 2.0 (3LO)
access token from the team's one-click connection (``autune_core.jira_access``,
#82) addressed by cloud id through ``api.atlassian.com``. The original
constructor (site URL, email, API token) is kept for a developer's own site.

What leaves is what an issue needs -- the item's description as the summary,
its due date, the assignee's Jira account -- never a transcript. Looking the
assignee up sends their email address to the team's own Jira as a search
query; that is addressing, declared below, not content.
"""

from __future__ import annotations

from base64 import b64encode
from datetime import date
from typing import Any

from .base import HttpClient
from .errors import PermanentIntegrationError

DESTINATION = "jira"

API_ROOT = "https://api.atlassian.com/ex/jira"

STATUS_CATEGORIES = frozenset({"new", "indeterminate", "done"})
"""Jira's three fixed status categories. Workflows name their statuses freely
("To Do", "할 일", "Backlog"); the category is what every workflow shares."""


class JiraClient(HttpClient):
    service = "jira"

    addressing = frozenset({"query", "project", "issuetype", "assignee", "transition"})
    """Keys whose values address the request: a user search's email, and the
    objects naming a project, an issue type, an assignee's account or a
    transition. Named by their parents rather than by ``key``/``id``, which are
    generic enough that a later field carrying content under one of them would
    go unchecked (#458 review). Everything else is checked."""

    def __init__(self, base_url: str, email: str, token: str) -> None:
        credentials = b64encode(f"{email}:{token}".encode()).decode()
        super().__init__(
            f"{base_url.rstrip('/')}/rest/api/3",
            {"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
        )

    @classmethod
    def for_cloud(cls, access_token: str, cloud_id: str) -> JiraClient:
        """A client on a team's 3LO connection -- the product's path."""
        client = cls.__new__(cls)
        HttpClient.__init__(
            client,
            f"{API_ROOT}/{cloud_id}/rest/api/3",
            {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        )
        return client

    # --- people ------------------------------------------------------------------

    def find_account_id(self, email: str) -> str | None:
        """The Jira account for an email, or ``None`` -- a site that hides
        emails, or someone with no account there. The caller leaves the issue
        unassigned rather than guessing."""
        # The endpoint answers with a JSON list; ``request`` is typed for objects.
        found: Any = self.request("GET", "/user/search", params={"query": email})
        people: list[dict[str, Any]] = found if isinstance(found, list) else []
        for person in people:
            if person.get("accountType") == "atlassian" and person.get("accountId"):
                return str(person["accountId"])
        return None

    # --- issues ------------------------------------------------------------------

    def create_issue(
        self, project_key: str, issue_type: str, summary: str, description: str
    ) -> str:
        body: dict[str, Any] = {
            "fields": {
                "project": {"key": project_key},
                "issuetype": {"name": issue_type},
                "summary": summary,
                "description": _doc(description),
            }
        }
        return str(self.request("POST", "/issue", json=body).get("key", ""))

    def create_task(
        self,
        project_key: str,
        summary: str,
        *,
        description: str = "",
        due_date: date | None = None,
        assignee_account_id: str | None = None,
        issue_type: str = "Task",
    ) -> str:
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "issuetype": {"name": issue_type},
            "summary": summary,
            "duedate": due_date.isoformat() if due_date else None,
            "assignee": {"accountId": assignee_account_id} if assignee_account_id else None,
        }
        if description:
            fields["description"] = _doc(description)
        return str(self.request("POST", "/issue", json={"fields": fields}).get("key", ""))

    def update_task(
        self,
        issue_key: str,
        summary: str,
        *,
        due_date: date | None,
        assignee_account_id: str | None,
        keep_assignee: bool = False,
        description: str | None = None,
    ) -> bool:
        """Rewrite the fields Autune owns. ``False`` when the issue is gone --
        deleted in Jira -- so the caller can make a new one.

        ``keep_assignee`` leaves Jira's assignee as it is: for a person Autune
        could not find in Jira, whom someone may have assigned by hand there.

        ``description`` rewrites the description ``create_task`` wrote, ``""``
        clearing it; ``None`` leaves it. Without it an item whose text changed --
        a person deleting their own speech (#587, #601) -- kept the old text in
        Jira whenever it had been too long or multi-line for the summary."""
        fields: dict[str, Any] = {
            "summary": summary,
            "duedate": due_date.isoformat() if due_date else None,
        }
        if description is not None:
            fields["description"] = _doc(description) if description else None
        if not keep_assignee:
            fields["assignee"] = {"accountId": assignee_account_id} if assignee_account_id else None
        try:
            self.request("PUT", f"/issue/{issue_key}", json={"fields": fields})
        except PermanentIntegrationError as exc:
            if exc.details.get("upstream_status") == 404:
                return False
            raise
        return True

    def transition(self, issue_key: str, transition_id: str) -> None:
        self.request(
            "POST", f"/issue/{issue_key}/transitions", json={"transition": {"id": transition_id}}
        )

    def add_comment(self, issue_key: str, text: str) -> None:
        self.request("POST", f"/issue/{issue_key}/comment", json={"body": _doc(text)})

    def status_category(self, issue_key: str) -> str | None:
        """The category (``new``, ``indeterminate``, ``done``) of the status
        the issue is in now."""
        found = self.request("GET", f"/issue/{issue_key}", params={"fields": "status"})
        status = (found.get("fields") or {}).get("status") or {}
        key = (status.get("statusCategory") or {}).get("key")
        return str(key) if key else None

    def move_to_category(self, issue_key: str, category: str) -> bool:
        """Move the issue into a status of ``category`` (``new``,
        ``indeterminate``, ``done``) through whatever transition its workflow
        offers. ``False`` when the workflow offers none -- the issue stays where
        it is, which is the team's workflow to decide.

        An issue already in a status of that category is left alone. A category
        can hold several statuses -- In Progress and In Review, Backlog and
        Selected for Development -- and the one a person chose in Jira is theirs
        (#458 review)."""
        if category not in STATUS_CATEGORIES:
            raise ValueError(f"unknown status category {category!r}")
        if self.status_category(issue_key) == category:
            return True
        found = self.request("GET", f"/issue/{issue_key}/transitions")
        for option in found.get("transitions", []):
            to = option.get("to", {})
            if to.get("statusCategory", {}).get("key") == category:
                self.transition(issue_key, str(option["id"]))
                return True
        return False


def _doc(text: str) -> dict[str, Any]:
    """Atlassian Document Format for one paragraph of plain text."""
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
