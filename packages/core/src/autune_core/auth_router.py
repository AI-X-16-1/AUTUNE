"""``/api/auth`` — sign-in endpoints.

This router is the one deliberate exception to "nobody edits apps/api/main.py":
auth is cross-cutting, owned by the whole team, and does not belong to any
module, so ``apps/api`` mounts it by name alongside the module routers.

Flow:

- ``GET /google/start``    -> 307 to Google's consent screen, and the
                              ``state`` in a short-lived cookie
- ``GET /google/callback`` -> check that cookie against the query's ``state``,
                              upsert the user, set the session cookie, 303 back
                              to the web app
- ``GET /providers``       -> which providers this server can complete, so the
                              sign-in screen disables the rest
- ``POST /logout``         -> clear the cookie (the token itself stays valid
                              until it expires; see environments.md)
- ``GET /me``              -> the current user (used by the web app to bootstrap)
- ``GET /google/calendar/start``       -> Google's consent for the person's own
                                         calendar, offline; the same callback
                                         finishes it (purpose ``calendar``)
- ``GET /google/calendar``             -> whether *this* person connected one
- ``POST /google/calendar/disconnect`` -> revoke at Google, then forget
"""

from __future__ import annotations

import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from .auth import CurrentUser, clear_session_cookie, issue_token, set_session_cookie
from .auth_service import upsert_user_from_google
from .db import get_session
from .errors import AutuneError, PermissionDeniedError
from .logging import get_logger
from .oauth.google import CALENDAR_SCOPE, GoogleOAuthClient, get_google_client
from .oauth.state import STATE_TTL_SECONDS, OAuthTransaction, StateStore, get_state_store
from .settings import get_settings
from .user_integrations import (
    disconnect_user_integration,
    load_user_integration,
    save_user_integration,
)

log = get_logger(__name__)

router = APIRouter()

# The state also rides in this cookie so the callback only accepts it from the
# browser that started the flow. Redis alone proves the state was issued, not
# to whom: without the cookie, an attacker's own callback URL, opened by a
# victim, would sign the victim in as the attacker (login CSRF).
STATE_COOKIE = "autune_oauth_state"


def _safe_redirect_target(raw: str) -> str:
    """Only ever redirect to a path on our own web app, never an absolute URL.

    ``//host`` is refused because a browser reads it as an absolute URL on the
    current scheme. ``\\`` and ``:`` are refused too: browsers have historically
    treated a backslash as a separator, and a colon is how a scheme starts.
    Every ``redirect_to`` this app sends is a plain screen path -- ``/``,
    ``/meetings/<id>``, ``/dashboard`` -- so neither character costs anything
    (@PARKJAEKYUNG0525 on #425).
    """
    if raw.startswith("/") and not raw.startswith("//") and not set(raw) & {"\\", ":"}:
        return raw
    return "/"


def _web_url(path: str) -> str:
    """Join a checked path onto the web app's origin, and prove it stayed there.

    **Not ``urljoin``.** It joins a *reference*, and a reference that looks
    absolute replaces the base entirely. ``_safe_redirect_target`` passes
    ``/https://evil.example/phish`` -- it starts with one ``/`` -- and stripping
    that ``/`` for the join leaves ``https://evil.example/phish``, which
    ``urljoin`` returns unchanged. The callback then answered
    ``303 Location: https://evil.example/phish`` after a real Google sign-in:
    an open redirect a person reaches from our own domain, having just done
    something that looks exactly like signing in (@PARKJAEKYUNG0525 on #425).

    ``path`` is guaranteed to start with exactly one ``/``, so concatenation is
    the whole join.

    The origin check after it is deliberately unreachable. Nothing this
    function can be handed today survives concatenation and still lands off
    origin, so no test covers that branch — which is the honest way to describe
    it rather than counting it as a third tested layer. It is here because the
    two layers above are both *arguments* that a value is safe, and this is the
    only line that *checks*. A future `redirect_to` carrying something nobody
    thought of gets refused by arithmetic instead of by reasoning.
    """
    base = get_settings().web_base_url.rstrip("/")
    candidate = base + ("/" + path.lstrip("/"))
    if urlsplit(candidate)[:2] != urlsplit(base)[:2]:  # (scheme, netloc)
        log.warning("auth_redirect_target_left_the_origin")
        return base + "/"
    return candidate


@router.get("/google/start")
def google_start(
    request: Request,
    store: Annotated[StateStore, Depends(get_state_store)],
    google: Annotated[GoogleOAuthClient, Depends(get_google_client)],
    redirect_to: Annotated[str, Query()] = "/",
) -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    store.put(state, OAuthTransaction(nonce=nonce, redirect_to=_safe_redirect_target(redirect_to)))
    response = RedirectResponse(google.authorization_url(state=state, nonce=nonce), status_code=307)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        # Lax still rides on Google's top-level GET back to the callback.
        samesite="lax",
        path=_callback_path(request),
    )
    return response


@router.get("/google/callback")
def google_callback(
    request: Request,
    state: Annotated[str, Query()],
    store: Annotated[StateStore, Depends(get_state_store)],
    google: Annotated[GoogleOAuthClient, Depends(get_google_client)],
    session: Annotated[Session, Depends(get_session)],
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    autune_oauth_state: Annotated[str | None, Cookie()] = None,
) -> Response:
    response: Response
    try:
        response = _complete_sign_in(
            state, autune_oauth_state, store, google, session, code=code, error=error
        )
    except AutuneError as exc:
        # Answered here rather than by the app's handler so the state cookie is
        # cleared on a failed callback too.
        response = JSONResponse(status_code=exc.status_code, content=exc.to_dict())
    response.delete_cookie(STATE_COOKIE, path=_callback_path(request))
    return response


def _callback_path(request: Request) -> str:
    """The callback's path as the browser sees it; the web app's /api proxy keeps it."""
    return request.url_for("google_callback").path


def _complete_sign_in(
    state: str,
    state_cookie: str | None,
    store: StateStore,
    google: GoogleOAuthClient,
    session: Session,
    *,
    code: str | None,
    error: str | None,
) -> RedirectResponse:
    # Checked before Redis, so a request from another browser never spends it.
    if state_cookie is None or not secrets.compare_digest(state_cookie.encode(), state.encode()):
        raise PermissionDeniedError("sign-in was not started in this browser")

    transaction = store.pop(state)
    if transaction is None:
        raise PermissionDeniedError("sign-in state is unknown or has expired")

    if transaction.purpose == "calendar":
        return _finish_calendar_connect(transaction, google, session, code=code, error=error)
    if transaction.purpose != "sign_in":
        # Other flows (Jira, Notion, Slack) share this store; their state is
        # never a Google sign-in, even with the cookie and query both set.
        raise PermissionDeniedError("this state did not start a Google sign-in")

    if error or not code:
        raise PermissionDeniedError("Google sign-in did not complete")

    identity = google.verify(google.exchange_code(code), nonce=transaction.nonce)
    if not identity.email_verified:
        raise PermissionDeniedError("this Google account's email is not verified")

    user = upsert_user_from_google(session, identity)
    log.info("auth_google_signed_in", user_id=user.id)

    response = RedirectResponse(_web_url(transaction.redirect_to), status_code=303)
    set_session_cookie(response, issue_token(user.id))
    return response


@router.get("/providers")
def providers() -> dict[str, bool]:
    return {"google": get_settings().google_sign_in_configured}


@router.post("/logout", status_code=204)
def logout() -> Response:
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/me")
def me(user: CurrentUser) -> dict[str, str]:
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


# --------------------------------------------------------------------------- #
# A person's own Google Calendar (#435): one click, their own grant
# --------------------------------------------------------------------------- #


@router.get("/google/calendar/start")
def google_calendar_start(
    request: Request,
    user: CurrentUser,
    store: Annotated[StateStore, Depends(get_state_store)],
    google: Annotated[GoogleOAuthClient, Depends(get_google_client)],
    redirect_to: Annotated[str, Query()] = "/",
) -> RedirectResponse:
    """Send a signed-in person to Google to let Autune put their own tasks'
    due dates on their own calendar.

    The same flow and callback as sign-in -- the same ``state`` cookie binding,
    so a callback from another browser is refused -- with the person's id kept
    in the transaction from *this* request's session. The callback therefore
    stores the grant for whoever started, never for whoever finishes.
    """
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    store.put(
        state,
        OAuthTransaction(
            nonce=nonce,
            redirect_to=_safe_redirect_target(redirect_to),
            purpose="calendar",
            user_id=user.id,
        ),
    )
    url = google.authorization_url(
        state=state, nonce=nonce, scope=f"openid {CALENDAR_SCOPE}", offline=True
    )
    response = RedirectResponse(url, status_code=307)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        path=_callback_path(request),
    )
    return response


def _finish_calendar_connect(
    transaction: OAuthTransaction,
    google: GoogleOAuthClient,
    session: Session,
    *,
    code: str | None,
    error: str | None,
) -> RedirectResponse:
    """A calendar connect that fails goes back to the screen it started from
    with ``?calendar=failed``, not to a JSON error: the person pressed a button
    on that screen and is still signed in there. Declining on Google's screen,
    unticking the calendar box, or Google withholding a refresh token all land
    here. Sign-in's own failures are unchanged."""
    try:
        if error or not code:
            raise PermissionDeniedError("Google calendar access was not granted")
        return _complete_calendar_connect(transaction, google, session, code=code)
    except AutuneError as exc:
        log.info(
            "auth_google_calendar_connect_failed", user_id=transaction.user_id, reason=exc.code
        )
        return RedirectResponse(
            _web_url(_with_query(transaction.redirect_to, "calendar=failed")), status_code=303
        )


def _with_query(path: str, pair: str) -> str:
    return path + ("&" if "?" in path else "?") + pair


def _complete_calendar_connect(
    transaction: OAuthTransaction,
    google: GoogleOAuthClient,
    session: Session,
    *,
    code: str,
) -> RedirectResponse:
    if not transaction.user_id:
        raise PermissionDeniedError("calendar connect was not started by a signed-in person")
    grant = google.exchange_grant(code)
    # The ID token proves this code answered *our* request (nonce), not which
    # Google account it was: someone may keep their calendar on another account,
    # and the consent asks for no ``email``, so none is required (#452 review).
    claims = google.verify_request(grant.id_token, nonce=transaction.nonce)
    account = str(claims["sub"])
    if CALENDAR_SCOPE not in grant.scopes:
        raise PermissionDeniedError("calendar access was not granted")
    if not grant.refresh_token:
        raise PermissionDeniedError("Google granted no offline access; connect again")
    previous = load_user_integration(session, transaction.user_id, "calendar")
    previous_account = previous.config.get("google_sub") if previous is not None else None
    if (
        previous is not None
        and previous.secret
        and previous_account is not None
        and previous_account != account
    ):
        # A calendar moved to another Google account: end the grant it replaces
        # rather than leave it valid and unknown to us. Never for the same
        # account -- Google's revoke ends everything that account granted
        # Autune, the refresh token just received included, and ``prompt=
        # consent`` hands out a new token on every connect, so comparing tokens
        # cannot tell the two apart (#452 review). A grant saved before the
        # account was recorded is left alone for the same reason.
        google.revoke(previous.secret)
    save_user_integration(
        session,
        transaction.user_id,
        "calendar",
        secret=grant.refresh_token,
        # ``sub`` is Google's stable account id, not a credential; it is kept
        # only so the next connect can tell a new account from the same one.
        config={"calendar_id": "primary", "google_sub": account},
    )
    log.info("auth_google_calendar_connected", user_id=transaction.user_id)
    return RedirectResponse(
        _web_url(_with_query(transaction.redirect_to, "calendar=connected")), status_code=303
    )


@router.get("/google/calendar")
def google_calendar_status(
    user: CurrentUser, session: Annotated[Session, Depends(get_session)]
) -> dict[str, bool]:
    """Whether the signed-in person has connected their own calendar -- theirs
    only; there is no way to ask about anyone else."""
    grant = load_user_integration(session, user.id, "calendar")
    return {"connected": grant is not None and bool(grant.secret)}


@router.post("/google/calendar/disconnect")
def google_calendar_disconnect(
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    google: Annotated[GoogleOAuthClient, Depends(get_google_client)],
) -> dict[str, bool]:
    """Revoke the grant at Google, then forget it here (#444 review). Our copy
    goes even when Google cannot be reached; ``revoked`` says whether Google
    confirmed, so a person knows to check their Google account otherwise.

    The consent used ``include_granted_scopes``, so revoking this token can end
    the whole grant that Google account gave Autune -- sign-in scopes included.
    The next sign-in with that account then shows Google's consent screen again;
    nothing else changes."""
    grant = load_user_integration(session, user.id, "calendar")
    revoked = bool(grant and grant.secret and google.revoke(grant.secret))
    disconnect_user_integration(session, user.id, "calendar")
    log.info("auth_google_calendar_disconnected", user_id=user.id, revoked=revoked)
    return {"connected": False, "revoked": revoked}
