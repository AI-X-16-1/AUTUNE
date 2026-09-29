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
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import CurrentUser, clear_session_cookie, issue_token, set_session_cookie
from .auth_service import upsert_user_from_google
from .db import get_session
from .entities import Meeting, TeamMember
from .errors import AutuneError, NotFoundError, PermissionDeniedError
from .integrations_config import disconnect_integration, load_integration, save_integration
from .jira_connection import JIRA, jira_access
from .logging import get_logger
from .oauth.atlassian import AtlassianOAuthClient, get_atlassian_client
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


# --------------------------------------------------------------------------- #
# A team's Jira (#82, #428): one click, Atlassian OAuth 2.0 (3LO)
# --------------------------------------------------------------------------- #


def _team_of(session: Session, user_id: str, meeting_id: str) -> str:
    """The team of a meeting the person belongs to. The web app knows which
    meeting a screen is about, not which team; membership is checked here so
    nobody connects or reads another team's Jira."""
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    member = session.scalar(
        select(TeamMember.user_id).where(
            TeamMember.team_id == meeting.team_id, TeamMember.user_id == user_id
        )
    )
    if member is None:
        raise PermissionDeniedError("not a member of this meeting's team")
    return meeting.team_id


def _jira_callback_path(request: Request) -> str:
    return request.url_for("jira_callback").path


@router.get("/jira/start")
def jira_start(
    request: Request,
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    store: Annotated[StateStore, Depends(get_state_store)],
    atlassian: Annotated[AtlassianOAuthClient, Depends(get_atlassian_client)],
    meeting_id: Annotated[str, Query()],
    redirect_to: Annotated[str, Query()] = "/",
) -> RedirectResponse:
    """Send a team member to Atlassian to connect the team's Jira.

    The grant is theirs (#82): it lasts while their Atlassian account does, and
    the connection says who made it (``connected_by``). Same browser-bound
    ``state`` as Google sign-in, cookie scoped to the Jira callback."""
    team_id = _team_of(session, user.id, meeting_id)
    state = secrets.token_urlsafe(32)
    store.put(
        state,
        OAuthTransaction(
            nonce="",
            redirect_to=_safe_redirect_target(redirect_to),
            purpose="jira",
            user_id=user.id,
            team_id=team_id,
        ),
    )
    response = RedirectResponse(atlassian.authorization_url(state=state), status_code=307)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        path=_jira_callback_path(request),
    )
    return response


@router.get("/jira/callback")
def jira_callback(
    request: Request,
    state: Annotated[str, Query()],
    store: Annotated[StateStore, Depends(get_state_store)],
    atlassian: Annotated[AtlassianOAuthClient, Depends(get_atlassian_client)],
    session: Annotated[Session, Depends(get_session)],
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    autune_oauth_state: Annotated[str | None, Cookie()] = None,
) -> Response:
    response: Response
    if autune_oauth_state is None or not secrets.compare_digest(
        autune_oauth_state.encode(), state.encode()
    ):
        response = JSONResponse(
            status_code=403,
            content=PermissionDeniedError("Jira connect was not started in this browser").to_dict(),
        )
    else:
        transaction = store.pop(state)
        if transaction is None or transaction.purpose != "jira" or not transaction.team_id:
            response = JSONResponse(
                status_code=403,
                content=PermissionDeniedError("Jira connect state is unknown or expired").to_dict(),
            )
        else:
            response = _finish_jira_connect(transaction, atlassian, session, code=code, error=error)
    response.delete_cookie(STATE_COOKIE, path=_jira_callback_path(request))
    return response


def _finish_jira_connect(
    transaction: OAuthTransaction,
    atlassian: AtlassianOAuthClient,
    session: Session,
    *,
    code: str | None,
    error: str | None,
) -> RedirectResponse:
    """Store the team's grant and the site it reaches, then back to the screen
    with ``?jira=connected`` -- or ``?jira=failed`` for a decline, a grant
    without ``offline_access``, or a grant that reaches no Jira site.

    One project is picked automatically when the person can see exactly one;
    otherwise the screen asks which (``GET /jira`` lists them)."""
    assert transaction.team_id is not None
    try:
        if error or not code:
            raise PermissionDeniedError("Jira access was not granted")
        tokens = atlassian.exchange_code(code)
        if not tokens.refresh_token:
            raise PermissionDeniedError("Atlassian granted no offline access")
        sites = atlassian.sites(tokens.access_token)
        if not sites:
            raise PermissionDeniedError("this grant reaches no Jira site")
        site = sites[0]
        projects = atlassian.projects(tokens.access_token, site.cloud_id)
        save_integration(
            session,
            transaction.team_id,
            JIRA,
            secret=tokens.refresh_token,
            config={
                "cloud_id": site.cloud_id,
                "site_url": site.url,
                "site_name": site.name,
                "project_key": projects[0].key if len(projects) == 1 else None,
                "needs_reconnect": False,
            },
            connected_by=transaction.user_id,
        )
        log.info("auth_jira_connected", team_id=transaction.team_id, sites=len(sites))
        outcome = "connected"
    except AutuneError as exc:
        log.info("auth_jira_connect_failed", team_id=transaction.team_id, reason=exc.code)
        outcome = "failed"
    return RedirectResponse(
        _web_url(_with_query(transaction.redirect_to, f"jira={outcome}")), status_code=303
    )


@router.get("/jira")
def jira_status(
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    meeting_id: Annotated[str, Query()],
) -> dict[str, object]:
    """The team's Jira connection as a member sees it: which site, which project,
    and whether it needs someone to reconnect. With no project chosen yet, the
    projects to choose from."""
    team_id = _team_of(session, user.id, meeting_id)
    config = load_integration(session, team_id, JIRA)
    if config is None or not config.secret:
        return {"connected": False}
    answer: dict[str, object] = {
        "connected": True,
        "needs_reconnect": bool(config.config.get("needs_reconnect")),
        "site_name": config.config.get("site_name"),
        "project_key": config.config.get("project_key"),
    }
    if not answer["needs_reconnect"] and not answer["project_key"]:
        answer["projects"] = [{"key": p.key, "name": p.name} for p in _projects_for(team_id)]
    return answer


def _projects_for(team_id: str) -> list:
    access = jira_access(team_id)
    if access is None:
        return []
    return get_atlassian_client().projects(access.access_token, access.cloud_id)


@router.post("/jira/project")
def jira_choose_project(
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    meeting_id: Annotated[str, Query()],
    project_key: Annotated[str, Query()],
) -> dict[str, object]:
    """Which project confirmed items become issues in. Only a project the grant
    can actually see is accepted."""
    team_id = _team_of(session, user.id, meeting_id)
    config = load_integration(session, team_id, JIRA)
    if config is None:
        raise NotFoundError("integration", f"jira for team {team_id}")
    if project_key not in {p.key for p in _projects_for(team_id)}:
        raise PermissionDeniedError("that project is not visible to this Jira connection")
    save_integration(session, team_id, JIRA, config={**config.config, "project_key": project_key})
    return {"connected": True, "project_key": project_key}


@router.post("/jira/disconnect")
def jira_disconnect(
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    meeting_id: Annotated[str, Query()],
) -> dict[str, bool]:
    """Forget the team's Jira connection. Atlassian offers no API to revoke a
    3LO grant; the person who connected removes Autune from their Atlassian
    account's connected apps -- the answer says so (``revoked: false``)."""
    team_id = _team_of(session, user.id, meeting_id)
    disconnect_integration(session, team_id, JIRA)
    log.info("auth_jira_disconnected", team_id=team_id, user_id=user.id)
    return {"connected": False, "revoked": False}
