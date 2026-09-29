"""Creating the Notion databases a team's sync writes to, under one page.

A team connects Notion by sharing one page with the integration; everything
Autune writes lives in databases this module creates under that page. Three of
them: "액션 아이템", "결정" and "회의록", each with exactly the property names
the sync uses -- ``service.NOTION_PROPERTIES``,
``service.DECISION_NOTION_PROPERTIES`` and ``MINUTES_NOTION_PROPERTIES`` below
-- so the schema created here and the pages written later cannot drift apart.

**Why this is not in the dev route any more.** It started inside
``dev/routes.py`` (#402), the local-only page that connects Notion by hand until
S28 exists. S28's one-click connect (#428) needs the same step after its OAuth
callback, and ``packages/core`` cannot import a module (invariant 2), so the
consumer of the connect event #428 proposes will call ``provision_databases``
from here. The dev route is now one caller of it, not its owner.

**Why httpx and not ``packages/integrations``.** ``NotionClient`` has
``create_page`` and ``update_page`` -- what the sync needs. Creating a database
is a one-time setup call only B makes, and putting it in the shared package
would need team approval for code one module uses. What leaves here is three
fixed database titles and property names; no meeting content, so the outbound
check ``packages/integrations`` applies has nothing to catch.

**The 회의록 database has no writer yet.** ``ui-spec.md`` lists it for S28 ("a
page on confirmation") but not what the page holds; #428 item 7 proposes only
what the action and decision databases already receive, never a transcript.
Creating it now means a team connected today does not have to reconnect when
that writer lands.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from autune_extraction.service import DECISION_NOTION_PROPERTIES, NOTION_PROPERTIES

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

MINUTES_NOTION_PROPERTIES: Mapping[str, str] = {
    "title": "제목",
    "date": "날짜",
    "meeting": "회의",
}
"""The 회의록 database's properties, by the same key-to-name scheme as the other
two. ``meeting`` holds the meeting id, as it does in the action and decision
databases. The writer (#428 item 7) must import this map rather than spell the
names again."""

_ACTION_STATUS_OPTIONS = ["needs_confirmation", "todo", "in_progress", "done"]

DATABASES: tuple[tuple[str, str, Mapping[str, str], bool], ...] = (
    ("action_db_id", "액션 아이템", NOTION_PROPERTIES, True),
    ("decision_db_id", "결정", DECISION_NOTION_PROPERTIES, False),
    ("minutes_db_id", "회의록", MINUTES_NOTION_PROPERTIES, False),
)
"""(config key, database title, property names, status as a select) for each
database a connection gets, in creation order."""


class NotionSetupError(Exception):
    """Notion refused a setup call. ``message`` is Notion's own, when it sent one:
    it names what was wrong (bad page id, page not shared with the integration),
    which is what the person connecting needs to read."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def notion_client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=NOTION_API,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        timeout=10.0,
    )


def schema(names: Mapping[str, str], *, status_select: bool) -> dict[str, Any]:
    """A Notion database property schema from the name map the sync uses."""
    properties: dict[str, Any] = {names["title"]: {"title": {}}}
    for key, name in names.items():
        if key == "title":
            continue
        if key == "status" and status_select:
            properties[name] = {
                "select": {"options": [{"name": value} for value in _ACTION_STATUS_OPTIONS]}
            }
        elif key in ("confidence", "sources"):
            properties[name] = {"number": {}}
        elif key in ("due", "date"):
            properties[name] = {"date": {}}
        else:
            properties[name] = {"rich_text": {}}
    return properties


def create_database(
    client: httpx.Client,
    *,
    page_id: str,
    title: str,
    names: Mapping[str, str],
    status_select: bool,
) -> str:
    body = {
        "parent": {"page_id": page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": schema(names, status_select=status_select),
    }
    resp = client.post("/databases", json=body)
    if resp.status_code >= 400:
        # A proxy's HTML page instead of Notion's JSON must not turn into a
        # 500 (lsh2217, review of #402).
        try:
            message = resp.json().get("message")
        except ValueError:
            message = None
        raise NotionSetupError(resp.status_code, message or f"Notion answered {resp.status_code}")
    return str(resp.json()["id"])


def same_page(stored: object, page_id: str) -> bool:
    """Notion takes a page id with or without its dashes; so does this."""
    return isinstance(stored, str) and stored.replace("-", "") == page_id.replace("-", "")


def provision_databases(
    client: httpx.Client, *, page_id: str, stored: Mapping[str, Any]
) -> tuple[dict[str, str], list[str]]:
    """The Notion config for a connection to ``page_id``, and the config keys of
    the databases this call had to create.

    Idempotent for the same page: a database already recorded under it is kept,
    and only a missing one is created -- so a team connected before the 회의록
    database existed gets that one and keeps the other two. A different page,
    or a stored config that never recorded its page, gets all new databases,
    because the old ones may not be shared with the new token (lsh2217, review
    of #402).
    """
    reuse = same_page(stored.get("parent_page_id"), page_id)
    config: dict[str, str] = {}
    created: list[str] = []
    for key, title, names, status_select in DATABASES:
        existing = stored.get(key) if reuse else None
        if isinstance(existing, str) and existing:
            config[key] = existing
            continue
        config[key] = create_database(
            client, page_id=page_id, title=title, names=names, status_select=status_select
        )
        created.append(key)
    config["parent_page_id"] = page_id
    return config, created
