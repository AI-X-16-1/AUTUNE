"""A local-only page for connecting a team's Notion/Slack integration by hand.

Mirrors module A's own ``dev/routes.py``: mounted only under
``AUTUNE_ENV=local`` (see ``router.py``), not in the OpenAPI schema, no auth.
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
apart. Idempotent: a team that already has both ids in its stored config
gets them back unchanged rather than a second pair of databases.

Deleted the day S28 ships.
"""

from __future__ import annotations

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
        raise HTTPException(
            status_code=resp.status_code, detail=resp.json().get("message", resp.text)
        )
    return str(resp.json()["id"])


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
    page_id = body.page_id.strip().split("/")[-1].split("-")[-1].split("?")[0]

    existing = load_integration(session, body.team_id, "notion")
    action_db = existing.config.get("action_db_id") if existing else None
    decision_db = existing.config.get("decision_db_id") if existing else None

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
        config={"action_db_id": action_db, "decision_db_id": decision_db},
    )
    session.commit()
    return {
        "status": "connected",
        "service": "notion",
        "team_id": body.team_id,
        "action_db_id": action_db,
        "decision_db_id": decision_db,
    }


class ConnectSlack(BaseModel):
    team_id: str
    bot_token: str


@router.post("/connect-slack", include_in_schema=False)
def connect_slack(body: ConnectSlack, session: SessionDep) -> dict[str, str]:
    """Saved for completeness -- module B has no consumer for this yet.

    The confirmation DM (#12) is blocked on #70 (a speaker's Slack account)
    and #30 (a team Slack client), so connecting Slack here does not make
    anything happen. It is here so a developer testing #12's ambiguous path
    is not left wondering whether the missing piece is the connection or the
    feature; it is the feature.
    """
    save_integration(session, body.team_id, "slack", secret=body.bot_token, config={})
    session.commit()
    return {"status": "connected", "service": "slack", "team_id": body.team_id}
