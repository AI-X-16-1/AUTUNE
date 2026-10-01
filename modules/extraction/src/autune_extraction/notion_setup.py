"""Creating the Notion databases a team's sync writes to, under one page.

A team connects Notion by sharing one page with the integration. Under it this
module makes a page of Autune's own, titled "Autune", and everything Autune
writes lives in databases inside that page -- so the team finds it all in one
place and the page they shared keeps only that one child (decided with the
user, 2026-10-01). Three databases: "액션 아이템", "결정" and "회의록", each
with exactly the property names
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
from sqlalchemy import text
from sqlalchemy.orm import Session

from autune_core.integrations_config import IntegrationConfig
from autune_extraction.models import ExtNotionTarget
from autune_extraction.service import (
    DECISION_NOTION_PROPERTIES,
    NOTION_PROPERTIES,
    NOTION_STATUS_LABELS,
)

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

_ACTION_STATUS_OPTIONS = list(NOTION_STATUS_LABELS.values())

HOME_TITLE = "Autune"
HOME_INTRO = (
    "Autune이 회의에서 확정한 액션 아이템과 결정을 이 페이지 아래 데이터베이스에 "
    "정리합니다. 데이터베이스와 속성 이름을 바꾸면 동기화가 멈추니 그대로 두세요."
)
"""The page every database goes in, and the one line on it. Fixed text: no
meeting content leaves here."""

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


def _refused(resp: httpx.Response) -> NotionSetupError:
    # A proxy's HTML page instead of Notion's JSON must not turn into a 500
    # (lsh2217, review of #402).
    try:
        message = resp.json().get("message")
    except ValueError:
        message = None
    return NotionSetupError(resp.status_code, message or f"Notion answered {resp.status_code}")


def create_home_page(client: httpx.Client, *, page_id: str | None) -> str:
    """The "Autune" page holding the databases: under the page the team shared,
    or -- ``page_id`` ``None``, nothing shared -- at the top of the workspace,
    which Notion makes one of the connecting person's private pages. Only a
    public (OAuth) integration may do that, which the one-click connect is."""
    body = {
        "parent": {"page_id": page_id} if page_id else {"workspace": True},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": HOME_TITLE}}]}},
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": HOME_INTRO}}]},
            }
        ],
    }
    resp = client.post("/pages", json=body)
    if resp.status_code >= 400:
        raise _refused(resp)
    return str(resp.json()["id"])


def home_of(client: httpx.Client, database_id: str) -> str | None:
    """The page a database Autune made sits in, so a missing one is added
    beside it. ``None`` when Notion no longer has it under a page."""
    resp = client.get(f"/databases/{database_id}")
    if resp.status_code >= 400:
        raise _refused(resp)
    parent = resp.json().get("parent") or {}
    return str(parent["page_id"]) if parent.get("type") == "page_id" else None


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
        raise _refused(resp)
    return str(resp.json()["id"])


def same_page(stored: object, page_id: str) -> bool:
    """Notion takes a page id with or without its dashes; so does this."""
    return isinstance(stored, str) and stored.replace("-", "") == page_id.replace("-", "")


def provision_databases(
    client: httpx.Client, *, page_id: str, stored: Mapping[str, Any], home: str | None = None
) -> tuple[dict[str, str], list[str]]:
    """The Notion config for a connection to ``page_id``, and the config keys of
    the databases this call had to create.

    Idempotent for the same page: a database already recorded under it is kept,
    and only a missing one is created -- so a team connected before the 회의록
    database existed gets that one and keeps the other two. A different page,
    or a stored config that never recorded its page, gets all new databases,
    because the old ones may not be shared with the new token (lsh2217, review
    of #402).

    New databases go in a new "Autune" page under ``page_id``
    (``create_home_page``); a missing one goes beside the ones kept, in
    whatever page holds them -- a team set up before the "Autune" page existed
    keeps its databases directly under the page it shared. ``home`` is an
    "Autune" page already made, for ``page_id`` itself to hold them
    (``notion_connect.set_up`` with nothing shared).
    """
    reuse = same_page(stored.get("parent_page_id"), page_id)
    config: dict[str, str] = {}
    for key, *_ in DATABASES:
        existing = stored.get(key) if reuse else None
        if isinstance(existing, str) and existing:
            config[key] = existing
    created: list[str] = []
    missing = [database for database in DATABASES if database[0] not in config]
    if missing:
        kept = next(iter(config.values()), None)
        home = (
            home
            or (home_of(client, kept) if kept else None)
            or create_home_page(client, page_id=page_id)
        )
        for key, title, names, status_select in missing:
            config[key] = create_database(
                client, page_id=home, title=title, names=names, status_select=status_select
            )
            created.append(key)
    config["parent_page_id"] = page_id
    return config, created


def shared_pages(client: httpx.Client, *, limit: int = 50) -> list[dict[str, str]]:
    """The pages the workspace shared with Autune on Notion's consent screen --
    what the team chooses a parent from. Rows of a database are left out: a
    database's pages (our own action items among them) are not a place to put
    new databases."""
    resp = client.post(
        "/search",
        json={"filter": {"property": "object", "value": "page"}, "page_size": limit},
    )
    if resp.status_code >= 400:
        raise NotionSetupError(resp.status_code, f"Notion answered {resp.status_code}")
    pages: list[dict[str, str]] = []
    for page in resp.json().get("results", []):
        if page.get("parent", {}).get("type") == "database_id" or page.get("archived"):
            continue
        pages.append({"id": str(page["id"]), "title": _title(page) or "(제목 없음)"})
    return pages


def _title(page: Mapping[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(part.get("plain_text", "") for part in prop.get("title", []))
    return ""


def database_id(
    session: Session, team_id: str, config: IntegrationConfig | None, key: str
) -> str | None:
    """A team's database id: from B's own ``ext_notion_targets`` (one-click
    setup), else from the connection's config (the local dev page, #401).

    A target row made in another workspace than the current connection's is
    not used: after a reconnect elsewhere its ids are databases the new token
    cannot see, and every sync would be refused (#467 review)."""
    target = current_target(session, team_id, config)
    if target is not None:
        value = getattr(target, key, None)
        return str(value) if value else None
    stored = config.config.get(key) if config is not None else None
    return str(stored) if stored else None


def workspace_of(config: IntegrationConfig | None) -> str | None:
    value = config.config.get("workspace_id") if config is not None else None
    return str(value) if value else None


def current_target(
    session: Session, team_id: str, config: IntegrationConfig | None
) -> ExtNotionTarget | None:
    """The team's target row, when it belongs to the workspace the team is
    connected to now."""
    target = session.get(ExtNotionTarget, team_id)
    if target is None or target.workspace_id != workspace_of(config):
        return None
    return target


def stored_targets(
    session: Session, team_id: str, config: IntegrationConfig | None
) -> dict[str, str]:
    """What ``provision_databases`` should treat as already made for this team
    -- nothing from another workspace."""
    target = current_target(session, team_id, config)
    if target is None:
        return {}
    return {
        "parent_page_id": target.parent_page_id,
        "action_db_id": target.action_db_id,
        "decision_db_id": target.decision_db_id,
        "minutes_db_id": target.minutes_db_id,
    }


def lock_setup(session: Session, team_id: str) -> None:
    """Hold the team's setup lock for the rest of ``session``'s transaction.

    Two people finishing the first setup at once both find no
    ``ext_notion_targets`` row, so a row lock has nothing to hold: both would
    make databases, leaving three orphans in Notion and one request failing on
    the primary key (#481). A transaction-scoped advisory lock keyed by team
    makes the second wait, then read the first one's row and reuse its
    databases. The key carries B's own namespace -- D keys its lineage lock on
    the bare team id, and the two have no reason to wait on each other.
    PostgreSQL only; SQLite (unit tests) has no such lock and runs one writer
    anyway."""
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"extraction.notion_setup:{team_id}"},
    )


def save_targets(
    session: Session, team_id: str, config: Mapping[str, str], *, workspace_id: str | None
) -> ExtNotionTarget:
    target = session.get(ExtNotionTarget, team_id)
    if target is None:
        target = ExtNotionTarget(team_id=team_id)
        session.add(target)
    target.workspace_id = workspace_id
    target.parent_page_id = config["parent_page_id"]
    target.action_db_id = config["action_db_id"]
    target.decision_db_id = config["decision_db_id"]
    target.minutes_db_id = config["minutes_db_id"]
    session.flush()
    return target
