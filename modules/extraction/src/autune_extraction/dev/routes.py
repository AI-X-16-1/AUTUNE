"""A local-only page for connecting a team's Notion integration by hand.

Mirrors module A's own ``dev/routes.py``, with one more gate: mounted only
under ``AUTUNE_ENV=local`` *and* ``AUTUNE_EXTRACTION_DEV_ROUTES=true`` (see
``router.dev_routes_enabled``), not in the OpenAPI schema, no auth. Without
the second gate a deployment that forgot ``AUTUNE_ENV`` would let anyone point
any team's sync at their own Notion workspace (lsh2217, review of #402).

S28 (Settings > Integrations) does not exist yet -- this exists so a developer
can put a real team_integrations row in the database without one, the same
reason module A's ``/dev/token`` exists for sign-in.

**One Notion page in, both databases created.** The first version of this
asked for the two database ids directly, which meant creating them by hand
in Notion first -- exactly the friction a settings page exists to remove.
This takes the integration token and one page id (shared with the
integration beforehand, Notion's own requirement) and creates "액션 아이템"
and "결정" as child databases under it, with the same property names
``service.NOTION_PROPERTIES``/``DECISION_NOTION_PROPERTIES`` already expect
-- so a page the sync writes to and the schema this creates cannot drift
apart. Idempotent for the same page: a team that already has both ids made
under it gets them back rather than a second pair. A different page -- or a
connection stored before the page was recorded -- gets a new pair, because the
old databases may not be shared with the new token (lsh2217, review of #402).

**It writes team_integrations, which modules otherwise never do.**
``data-model.md`` reserves that table for the settings layer; #401 makes a
local-only dev route the one exception until S28 ships.

**It calls Notion with httpx, not ``packages/integrations``.** ``NotionClient``
has ``create_page`` and ``update_page`` -- what the sync needs. Creating a
database is a one-time setup call that only this page makes, and adding it to
the shared package would put team-approved code in place for a page that is
deleted with S28. What leaves here is two fixed database titles and the
property names from ``service.py``; no meeting content, so the outbound
check ``packages/integrations`` would apply has nothing to catch.

Deleted the day S28 ships.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from autune_core import get_session
from autune_core.integrations_config import load_integration, save_integration
from autune_extraction.service import DECISION_NOTION_PROPERTIES, NOTION_PROPERTIES

from .page import PAGE

router = APIRouter()

_PAGE_ID = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$"
)
"""A Notion page id, dashed in the standard 8-4-4-4-12 grouping or not.

Notion always places the id as the last 32 hex characters of a page URL's
last path segment (title words come first, id last), so anchoring at the end
finds it correctly whether the title contains dashes or not. A title made of
hex-looking words ("Cafe Deadbeef Notes") cannot false-match: it would need to
end in exactly this 32-character run in this exact grouping, which no real
title does.
"""


def _parse_page_id(raw: str) -> str:
    """A bare Notion page id from either a bare id or a full page URL.

    Found in review of #342 (lsh2217): the previous version was
    ``raw.split("/")[-1].split("-")[-1].split("?")[0]`` -- splitting on "-"
    after dropping the URL prefix correctly reaches the id in a titled URL,
    but then keeps only the text after the *last* dash of whatever remained.
    For a bare dashed id (the exact form Notion's own UI copies to the
    clipboard), that throws away everything but the final 12 of 36
    characters, and every call after this line silently used the wrong id.
    """
    candidate = raw.strip().split("?")[0].split("/")[-1]
    match = _PAGE_ID.search(candidate)
    if match is None:
        raise ValueError(f"{raw!r} does not contain a Notion page id")
    return match.group(0)


SessionDep = Annotated[Session, Depends(get_session)]

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

_ACTION_STATUS_OPTIONS = ["needs_confirmation", "todo", "in_progress", "done"]


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def page() -> str:
    return PAGE


def _schema(names: dict[str, str], *, status_select: bool) -> dict[str, Any]:
    """A Notion database property schema from the same name map the real
    sync uses (``NOTION_PROPERTIES``/``DECISION_NOTION_PROPERTIES``), so the
    database this creates always has exactly the columns the sync writes to."""
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
        elif key == "due":
            properties[name] = {"date": {}}
        else:
            properties[name] = {"rich_text": {}}
    return properties


def _create_database(
    client: httpx.Client,
    *,
    page_id: str,
    title: str,
    names: dict[str, str],
    status_select: bool,
) -> str:
    body = {
        "parent": {"page_id": page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": _schema(names, status_select=status_select),
    }
    resp = client.post("/databases", json=body)
    if resp.status_code >= 400:
        # Notion's own message, not ours -- it names what was wrong with the
        # request (bad page id, integration not shared with the page, ...),
        # which is exactly what someone filling in this form needs to read.
        # A proxy's HTML page instead of Notion's JSON must not turn that
        # into a 500 (lsh2217, review of #402).
        try:
            message = resp.json().get("message")
        except ValueError:
            message = None
        raise HTTPException(
            status_code=resp.status_code,
            detail=message or f"Notion answered {resp.status_code}",
        )
    return str(resp.json()["id"])


def _same_page(stored: object, page_id: str) -> bool:
    """Notion takes a page id with or without its dashes; so does this."""
    return isinstance(stored, str) and stored.replace("-", "") == page_id.replace("-", "")


class ConnectNotion(BaseModel):
    team_id: str
    token: str
    page_id: str
    """A Notion page id or URL, already shared with the integration (Notion's
    own requirement -- share the page with the integration by name in
    Notion's UI first, or every call here gets a 404 that reads like a bad
    id)."""


@router.post("/connect-notion", include_in_schema=False)
def connect_notion(body: ConnectNotion, session: SessionDep) -> dict[str, str]:
    try:
        page_id = _parse_page_id(body.page_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = load_integration(session, body.team_id, "notion")
    stored = existing.config if existing is not None else {}
    reuse = _same_page(stored.get("parent_page_id"), page_id)
    action_db = stored.get("action_db_id") if reuse else None
    decision_db = stored.get("decision_db_id") if reuse else None

    if not action_db or not decision_db:
        with httpx.Client(
            base_url=NOTION_API,
            headers={
                "Authorization": f"Bearer {body.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=10.0,
        ) as client:
            if not action_db:
                action_db = _create_database(
                    client,
                    page_id=page_id,
                    title="액션 아이템",
                    names=dict(NOTION_PROPERTIES),
                    status_select=True,
                )
            if not decision_db:
                decision_db = _create_database(
                    client,
                    page_id=page_id,
                    title="결정",
                    names=dict(DECISION_NOTION_PROPERTIES),
                    status_select=False,
                )

    save_integration(
        session,
        body.team_id,
        "notion",
        secret=body.token,
        config={
            "action_db_id": action_db,
            "decision_db_id": decision_db,
            "parent_page_id": page_id,
        },
    )
    session.commit()
    return {
        "status": "connected",
        "service": "notion",
        "team_id": body.team_id,
        "action_db_id": action_db,
        "decision_db_id": decision_db,
    }
