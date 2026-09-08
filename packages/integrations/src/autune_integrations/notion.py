"""Notion.

MVP surface: create a page in a database. Property mapping is configured per
workspace (screen S28) and filled in during W3 by the extraction owner.

PII masking applies to every write and cannot be disabled — the check runs on
every value that leaves.
"""

from __future__ import annotations

from typing import Any

from .base import HttpClient
from .privacy import check_outbound

DESTINATION = "notion"
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
        for value in _strings(properties):
            check_outbound(value, destination=DESTINATION)
        body = {"parent": {"database_id": database_id}, "properties": properties}
        return str(self.request("POST", "/pages", json=body).get("id", ""))


def _strings(value: Any) -> list[str]:
    """Every string anywhere in a nested payload, so none escapes the check."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []
