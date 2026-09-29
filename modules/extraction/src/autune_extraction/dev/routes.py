"""A local-only page for connecting a team's Notion and Google Calendar by hand.

Mirrors module A's own ``dev/routes.py``, with one more gate: mounted only
under ``AUTUNE_ENV=local`` *and* ``AUTUNE_EXTRACTION_DEV_ROUTES=true`` (see
``router.dev_routes_enabled``), not in the OpenAPI schema, no auth. Without
the second gate a deployment that forgot ``AUTUNE_ENV`` would let anyone point
any team's sync at their own Notion workspace (lsh2217, review of #402).

S28 (Settings > Integrations) does not exist yet -- this exists so a developer
can put a real team_integrations row in the database without one, the same
reason module A's ``/dev/token`` exists for sign-in.

**One Notion page in, the databases created.** This takes the integration
token and one page id (shared with the integration beforehand, Notion's own
requirement) and hands them to ``notion_setup.provision_databases``, which
creates "액션 아이템", "결정" and "회의록" under the page -- or keeps the ones
already made under it. That step lives in ``notion_setup`` because S28's
one-click connect needs it too (#428); this page is one caller of it.

**Google Calendar: one person's refresh token in, checked, stored as theirs.**
Calendars are per person (#435): each person's own confirmed tasks go on their
own calendar, through their own grant in ``user_integrations`` (#444). Until
S28's OAuth flow exists (#428) a developer gets a refresh token from Google's
OAuth Playground with the deployment's own client, scope ``calendar.events``,
and pastes it here with the user id it belongs to. The route refreshes it and
reads the next week before storing anything, so a wrong token or an unreadable
calendar is refused here rather than discovered by the first confirmed date.

**It writes team_integrations and user_integrations, which modules otherwise
never do.** ``data-model.md`` reserves both for the settings layer; #401 makes a
local-only dev route the one exception until S28 ships.

Deleted the day S28 ships.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from autune_core import get_session
from autune_core.integrations_config import load_integration, save_integration
from autune_core.settings import get_settings as get_core_settings
from autune_core.user_integrations import save_user_integration
from autune_extraction.calendar_sync import CALENDAR, forget_calendar_cursor
from autune_extraction.notion_setup import (
    NotionSetupError,
    notion_client,
    provision_databases,
)
from autune_extraction.service import notion_url
from autune_integrations import (
    CalendarClient,
    IntegrationError,
    ReconnectRequiredError,
    refresh_access_token,
)

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


class ConnectCalendar(BaseModel):
    user_id: str
    """Whose calendar: the account the refresh token was issued for."""
    refresh_token: str
    calendar_id: str = "primary"
    """``primary`` -- the person's own calendar -- unless they want Autune's
    events on another calendar of theirs."""


def _read_body[Body: BaseModel](raw: dict[str, Any], model: type[Body]) -> Body:
    """The request as ``model``, refused without repeating what was sent.

    Declared as the parameter type, a missing or wrong field made FastAPI's
    default 422, whose ``input`` carries the whole body -- the token included
    -- and ``apps/api`` has no handler that would strip it (PARKJAEKYUNG0525,
    review of #402). Validated here instead, the refusal names each field and
    what was wrong with it, never a value.
    """
    try:
        return model.model_validate(raw)
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
    body = _read_body(raw, ConnectNotion)
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


@router.post("/connect-calendar", include_in_schema=False)
def connect_calendar(raw: Annotated[dict[str, Any], Body()], session: SessionDep) -> dict[str, str]:
    body = _read_body(raw, ConnectCalendar)
    core = get_core_settings()
    client_id = core.google_client_id
    client_secret = core.google_client_secret
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="AUTUNE_GOOGLE_CLIENT_ID and AUTUNE_GOOGLE_CLIENT_SECRET are not set",
        )
    try:
        token = refresh_access_token(
            client_id=client_id, client_secret=client_secret, refresh_token=body.refresh_token
        )
        client = CalendarClient(token)
        try:
            now = datetime.now(UTC)
            upcoming = client.list_events(body.calendar_id, now, now + timedelta(days=7), limit=20)
        finally:
            client.close()
    except ReconnectRequiredError:
        raise HTTPException(
            status_code=401, detail="Google refused the refresh token -- make a new one"
        ) from None
    except IntegrationError as exc:
        status = exc.details.get("upstream_status") or 502
        raise HTTPException(
            status_code=int(status), detail=f"Google Calendar answered {status}"
        ) from None

    save_user_integration(
        session,
        body.user_id,
        CALENDAR,
        secret=body.refresh_token,
        config={"calendar_id": body.calendar_id},
    )
    # A reconnect starts the read-back afresh: a cursor from before a lapsed
    # grant may be older than Google keeps changes for (review of #441).
    forget_calendar_cursor(session, body.user_id)
    session.commit()
    return {
        "status": "connected",
        "service": CALENDAR,
        "user_id": body.user_id,
        "calendar_id": body.calendar_id,
        "upcoming": str(len(upcoming)),
    }
