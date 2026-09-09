"""``/api/auth`` — sign-in endpoints.

This router is the one deliberate exception to "nobody edits apps/api/main.py":
auth is cross-cutting, owned by the whole team, and does not belong to any
module, so ``apps/api`` mounts it by name alongside the module routers.

Flow:

- ``GET /google/start``    -> 307 to Google's consent screen
- ``GET /google/callback`` -> upsert the user, set the session cookie, 303 back
                              to the web app
- ``POST /logout``         -> clear the cookie
- ``GET /me``              -> the current user (used by the web app to bootstrap)
"""

from __future__ import annotations

import secrets
from typing import Annotated
from urllib.parse import urljoin

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from .auth import CurrentUser, clear_session_cookie, issue_token, set_session_cookie
from .auth_service import upsert_user_from_google
from .db import get_session
from .errors import PermissionDeniedError
from .logging import get_logger
from .oauth.google import GoogleOAuthClient, get_google_client
from .oauth.state import OAuthTransaction, StateStore, get_state_store
from .settings import get_settings

log = get_logger(__name__)

router = APIRouter()


def _safe_redirect_target(raw: str) -> str:
    """Only ever redirect to a path on our own web app, never an absolute URL."""
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


def _web_url(path: str) -> str:
    return urljoin(get_settings().web_base_url.rstrip("/") + "/", path.lstrip("/"))


@router.get("/google/start")
def google_start(
    store: Annotated[StateStore, Depends(get_state_store)],
    google: Annotated[GoogleOAuthClient, Depends(get_google_client)],
    redirect_to: Annotated[str, Query()] = "/",
) -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    store.put(state, OAuthTransaction(nonce=nonce, redirect_to=_safe_redirect_target(redirect_to)))
    return RedirectResponse(google.authorization_url(state=state, nonce=nonce), status_code=307)


@router.get("/google/callback")
def google_callback(
    state: Annotated[str, Query()],
    store: Annotated[StateStore, Depends(get_state_store)],
    google: Annotated[GoogleOAuthClient, Depends(get_google_client)],
    session: Annotated[Session, Depends(get_session)],
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    if error or not code:
        raise PermissionDeniedError("Google sign-in did not complete")

    transaction = store.pop(state)
    if transaction is None:
        raise PermissionDeniedError("sign-in state is unknown or has expired")

    identity = google.verify(google.exchange_code(code), nonce=transaction.nonce)
    if not identity.email_verified:
        raise PermissionDeniedError("this Google account's email is not verified")

    user = upsert_user_from_google(session, identity)
    log.info("auth_google_signed_in", user_id=user.id)

    response = RedirectResponse(_web_url(transaction.redirect_to), status_code=303)
    set_session_cookie(response, issue_token(user.id))
    return response


@router.post("/logout", status_code=204)
def logout() -> Response:
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/me")
def me(user: CurrentUser) -> dict[str, str]:
    return {"id": user.id, "email": user.email, "display_name": user.display_name}
