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

    def __init__(self, base_url: str, email: str, token: str) -> None:
        credentials = b64encode(f"{email}:{token}".encode()).decode()
        super().__init__(
            f"{base_url.rstrip('/')}/rest/api/3",
            {"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
        )

    def create_issue(
        self, project_key: str, issue_type: str, summary: str, description: str
    ) -> str:
        body: dict[str, Any] = {
            "fields": {
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
        }
        return str(self.request("POST", "/issue", json=body).get("key", ""))

    def transition(self, issue_key: str, transition_id: str) -> None:
        self.request(
            "POST", f"/issue/{issue_key}/transitions", json={"transition": {"id": transition_id}}
        )
