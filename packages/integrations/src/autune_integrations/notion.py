"""Notion.

MVP surface: create a page in a database. Property mapping is configured per
workspace (screen S28) and filled in during W3 by the extraction owner.

PII masking applies to every write and cannot be disabled — the check runs on
every value that leaves.
"""

from __future__ import annotations

from typing import Any, Literal

from .base import HttpClient
from .errors import PermanentIntegrationError

DESTINATION = "notion"

PageState = Literal["deleted", "archived", "live"]
API_VERSION = "2022-06-28"


class NotionClient(HttpClient):
    service = "notion"

    def __init__(self, token: str) -> None:
        super().__init__(
            "https://api.notion.com/v1",
            {
                "Authorization": f"Bearer {token}",
                "Notion-Version": API_VERSION,
                "Content-Type": "application/json",
            },
        )

    def create_page(self, database_id: str, properties: dict[str, Any]) -> str:
        body = {"parent": {"database_id": database_id}, "properties": properties}
        return str(self.request("POST", "/pages", json=body).get("id", ""))

    def update_page(self, page_id: str, properties: dict[str, Any]) -> None:
        """Overwrite a page's properties -- everything named in ``properties``,
        nothing else. A page's content already exists by the time this runs;
        this is the "keep Notion in sync with a later edit" half, not a second
        creation, so it is a PATCH to the page itself rather than another
        ``create_page`` against its database."""
        self.request("PATCH", f"/pages/{page_id}", json={"properties": properties})

    def page_state(self, page_id: str) -> PageState:
        """``"deleted"``, ``"archived"`` or ``"live"`` -- for a caller whose
        ``update_page`` was refused.

        Notion answers an archived page with 400, the same status as a
        malformed request, so the refusal alone cannot say what happened.
        ``"archived"`` covers the trash too: a person put it there, and can
        take it back out. ``"deleted"`` is a 404, which Notion also gives for a
        page that still exists but is no longer shared with the integration --
        either way nothing can write to it again. Any other refusal (a revoked
        token, say) raises.
        """
        try:
            page = self.request("GET", f"/pages/{page_id}")
        except PermanentIntegrationError as exc:
            if exc.details.get("upstream_status") == 404:
                return "deleted"
            raise
        if page.get("archived") or page.get("in_trash"):
            return "archived"
        return "live"
