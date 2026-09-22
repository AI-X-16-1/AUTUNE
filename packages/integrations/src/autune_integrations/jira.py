"""Jira.

MVP surface: create an issue and move it between states. Project, issue type,
assignee mapping and status mapping are configured per workspace (screen S28)
and filled in during W3 by the extraction owner.

Send what an issue needs — description, assignee, due date — never a transcript.
"""

from __future__ import annotations

from base64 import b64encode
from typing import Any

from .base import HttpClient

DESTINATION = "jira"


class JiraClient(HttpClient):
    service = "jira"

    addressing = frozenset({"project", "issuetype", "assignee", "accountId", "duedate"})
    """Project key, issue type, the resolved Jira account id and the due date
    are feature-supplied routing, the same reasoning as ``CalendarClient``'s
    ``attendees`` -- none of it is meeting content, and a due date or an
    account id is exactly the shape a phone-number or national-id pattern can
    false-positive on. ``summary`` and ``description`` are not declared, so
    they are checked -- that is the transcript-derived half of the request."""

    def __init__(self, base_url: str, email: str, token: str) -> None:
        credentials = b64encode(f"{email}:{token}".encode()).decode()
        super().__init__(
            f"{base_url.rstrip('/')}/rest/api/3",
            {"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
        )

    def create_issue(
        self,
        project_key: str,
        issue_type: str,
        summary: str,
        description: str,
        *,
        assignee_account_id: str | None = None,
        due_date: str | None = None,
    ) -> str:
        """``assignee_account_id`` is a Jira Cloud account id, not an email or a
        name -- the team's own assignee mapping (screen S28) resolves one from
        our ``assignee_id``/``assignee_label`` before this is called. Left
        unset when there is no mapping: an unassigned issue in the backlog is
        the honest answer, not a guess. ``due_date`` is ``YYYY-MM-DD``, Jira's
        own ``duedate`` field -- a standard field on every project, unlike
        status transitions, which are per-workflow and go through
        ``transition()`` instead.
        """
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "issuetype": {"name": issue_type},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": description}]}
                ],
            },
        }
        if assignee_account_id:
            fields["assignee"] = {"accountId": assignee_account_id}
        if due_date:
            fields["duedate"] = due_date
        return str(self.request("POST", "/issue", json={"fields": fields}).get("key", ""))

    def transition(self, issue_key: str, transition_id: str) -> None:
        self.request(
            "POST", f"/issue/{issue_key}/transitions", json={"transition": {"id": transition_id}}
        )
