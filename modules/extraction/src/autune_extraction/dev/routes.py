"""A local-only page for connecting a team's Notion integration by hand.

Mirrors module A's own ``dev/routes.py``, with one more gate: mounted only
under ``AUTUNE_ENV=local`` *and* ``AUTUNE_EXTRACTION_DEV_ROUTES=true`` (see
``router.dev_routes_enabled``), not in the OpenAPI schema, no auth. Without
the second gate every ``local`` stack -- the demo one included, reachable by
whoever is on its network -- would let anyone point any team's sync at their
own Notion workspace (lsh2217, review of #402). A deployment that forgets
``AUTUNE_ENV`` is ``production`` since #446, so it gets neither gate.

S28 (Settings > Integrations) does not exist yet -- this exists so a developer
can put a real team_integrations row in the database without one, the same
reason module A's ``/dev/token`` exists for sign-in.

**One Notion page in, the databases created.** This takes the integration
token and one page id (shared with the integration beforehand, Notion's own
requirement) and hands them to ``notion_setup.provision_databases``, which
creates "액션 아이템", "결정" and "회의록" under the page -- or keeps the ones
already made under it. That step lives in ``notion_setup`` because S28's
one-click connect needs it too (#428); this page is one caller of it.

**It writes team_integrations, which modules otherwise never do.**
``data-model.md`` reserves that table for the settings layer; #401 makes a
local-only dev route the one exception until S28 ships.

Deleted the day S28 ships.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from autune_core import get_session
from autune_core.integrations_config import load_integration, save_integration
from autune_extraction.notion_setup import (
    NotionSetupError,
    notion_client,
    provision_databases,
)
from autune_extraction.service import notion_url

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
        # Not the input itself: the token field sits right above this one, and
        # a token pasted here would come back in the 400 body. privacy.md 6
        # treats an exception string as published (mkkim68, review of #402).
        raise ValueError("no Notion page id found -- paste the page's URL or its 32-character id")
    return match.group(0)


SessionDep = Annotated[Session, Depends(get_session)]


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def page() -> str:
    return PAGE


class ConnectNotion(BaseModel):
    team_id: str
    token: str
    page_id: str
    """A Notion page id or URL, already shared with the integration (Notion's
    own requirement -- share the page with the integration by name in
    Notion's UI first, or every call here gets a 404 that reads like a bad
    id)."""


def _read_body(raw: dict[str, Any]) -> ConnectNotion:
    """The request as ``ConnectNotion``, refused without repeating what was sent.

    Declared as the parameter type, a missing or wrong field made FastAPI's
    default 422, whose ``input`` carries the whole body -- the token included
    -- and ``apps/api`` has no handler that would strip it (PARKJAEKYUNG0525,
    review of #402). Validated here instead, the refusal names each field and
    what was wrong with it, never a value.
    """
    try:
        return ConnectNotion.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[
                {"loc": list(error["loc"]), "msg": error["msg"]}
                for error in exc.errors(include_input=False, include_url=False)
            ],
        ) from None


@router.post("/connect-notion", include_in_schema=False)
def connect_notion(raw: Annotated[dict[str, Any], Body()], session: SessionDep) -> dict[str, str]:
    body = _read_body(raw)
    try:
        page_id = _parse_page_id(body.page_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = load_integration(session, body.team_id, "notion")
    stored = existing.config if existing is not None else {}
    try:
        with notion_client(body.token) as client:
            config, created = provision_databases(client, page_id=page_id, stored=stored)
    except NotionSetupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None

    save_integration(session, body.team_id, "notion", secret=body.token, config=config)
    session.commit()
    return {
        "status": "connected",
        "service": "notion",
        "team_id": body.team_id,
        "action_db_id": config["action_db_id"],
        "decision_db_id": config["decision_db_id"],
        "minutes_db_id": config["minutes_db_id"],
        # What the page tells the person: whether it made the databases, found
        # the ones it made under this page before, or added only the missing
        # ones (a connection from before 회의록 existed) -- and where they are.
        "databases": "reused" if not created else "created" if len(created) == 3 else "added",
        "action_db_url": notion_url(config["action_db_id"]),
        "decision_db_url": notion_url(config["decision_db_id"]),
        "minutes_db_url": notion_url(config["minutes_db_id"]),
    }
