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

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import (
    SESSION_COOKIE,
    CurrentUser,
    _session_token,
    clear_session_cookie,
    current_user,
    issue_token,
    set_session_cookie,
)
from .auth_service import upsert_user_from_google
from .consents import Consent, consents_of, record_consents
from .crypto import ensure_configured
from .db import get_session
from .entities import Meeting, Team, TeamMember
from .errors import AutuneError, NotFoundError, PermissionDeniedError, ValidationError
from .integrations_config import (
    IntegrationConfig,
    disconnect_integration,
    load_integration,
    save_integration,
    teams_with,
)
from .jira_connection import JIRA, jira_access
from .logging import get_logger
from .oauth.atlassian import AtlassianOAuthClient, get_atlassian_client
from .oauth.google import (
    CALENDAR_SCOPE,
    GoogleOAuthClient,
    get_google_client,
    get_google_integration_client,
)
from .oauth.notion import NotionOAuthClient, get_notion_oauth_client
from .oauth.slack import (
    SlackAccountTakenError,
    SlackChannel,
    SlackInstall,
    SlackLinkNotConfirmedError,
    SlackOAuthClient,
    SlackTeamNotConnectedError,
    SlackWrongWorkspaceError,
    get_slack_oauth_client,
)
from .oauth.state import STATE_TTL_SECONDS, OAuthTransaction, StateStore, get_state_store
from .settings import get_settings
from .user_integrations import (
    disconnect_user_integration,
    load_user_integration,
    save_user_integration,
    users_linked_to_slack_member,
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


def integration_google(
    sign_in: Annotated[GoogleOAuthClient, Depends(get_google_client)],
) -> GoogleOAuthClient:
    """The Google client a person's own grant goes through -- calendar today.
    The deployment's integration client when it has one, the sign-in client
    otherwise. Whichever starts a connect has to finish it: the code Google
    returns can only be exchanged by the client it was issued to, and the ID
    token's ``aud`` names that client."""
    return get_google_integration_client() or sign_in


IntegrationGoogle = Annotated[GoogleOAuthClient, Depends(integration_google)]


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
    integration: IntegrationGoogle,
    session: Annotated[Session, Depends(get_session)],
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    autune_oauth_state: Annotated[str | None, Cookie()] = None,
) -> Response:
    response: Response
    try:
        response = _complete_sign_in(
            state,
            autune_oauth_state,
            store,
            google,
            session,
            code=code,
            error=error,
            integration=integration,
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
    integration: GoogleOAuthClient | None = None,
) -> RedirectResponse:
    # Checked before Redis, so a request from another browser never spends it.
    if state_cookie is None or not secrets.compare_digest(state_cookie.encode(), state.encode()):
        raise PermissionDeniedError("sign-in was not started in this browser")

    transaction = store.pop(state)
    if transaction is None:
        raise PermissionDeniedError("sign-in state is unknown or has expired")

    if transaction.purpose == "calendar":
        # Finished by the client that started it (``integration_google``).
        return _finish_calendar_connect(
            transaction, integration or google, session, code=code, error=error
        )
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
def me(user: CurrentUser, session: Annotated[Session, Depends(get_session)]) -> dict[str, object]:
    """Who is signed in, and the teams they belong to -- what S28 settings
    (#496) chooses a team's integrations from, with no meeting to name it."""
    teams = session.execute(
        select(Team.id, Team.name)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user.id)
        .order_by(Team.name, Team.id)
    ).all()
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "teams": [{"id": team_id, "name": name} for team_id, name in teams],
    }


# --------------------------------------------------------------------------- #
# What a person agreed to: recorded here, asked for by the consent page
# --------------------------------------------------------------------------- #


class _ConsentIn(BaseModel):
    document: str
    version: str


class _ConsentsIn(BaseModel):
    consents: list[_ConsentIn]


def _consents_answer(consents: list[Consent]) -> dict[str, object]:
    return {
        "consents": [
            {
                "document": c.document,
                "version": c.version,
                "agreed_at": c.agreed_at.isoformat() if c.agreed_at else None,
            }
            for c in consents
        ]
    }


@router.get("/consents")
def my_consents(
    user: CurrentUser, session: Annotated[Session, Depends(get_session)]
) -> dict[str, object]:
    """The documents and versions the signed-in person agreed to -- theirs
    only. The consent page compares this with what it requires; the server
    does not know which version is current (``autune_core.consents``)."""
    return _consents_answer(consents_of(session, user.id))


@router.post("/consents")
def agree(
    body: _ConsentsIn,
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """Record that the signed-in person agreed to each document and version
    named. Agreeing again changes nothing: the first time is the one kept.
    Nobody can agree for anyone else -- the person is the session's."""
    recorded = record_consents(
        session, user.id, [(item.document, item.version) for item in body.consents]
    )
    log.info("auth_consents_recorded", user_id=user.id, documents=len(body.consents))
    return _consents_answer(recorded)


# --------------------------------------------------------------------------- #
# A person's own Google Calendar (#435): one click, their own grant
# --------------------------------------------------------------------------- #


@router.get("/google/calendar/start")
def google_calendar_start(
    request: Request,
    user: CurrentUser,
    store: Annotated[StateStore, Depends(get_state_store)],
    google: IntegrationGoogle,
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


def _https_link(stored: object) -> str | None:
    """A stored address as something a screen may put in an ``href``: only an
    ``https://`` URL. What is stored came from the provider at connect time;
    this keeps a row that holds anything else from becoming a link a person
    clicks."""
    return stored if isinstance(stored, str) and stored.startswith("https://") else None


_SLACK_ID = re.compile(r"[A-Z0-9]{2,}")


def _slack_channel_link(config: dict) -> str | None:
    """Where the team's alert channel opens in Slack, built from the workspace
    and channel ids the install stored. ``None`` when either is missing or is
    not shaped like a Slack id -- an install from before the ids were kept, or
    a row somebody edited."""
    workspace = str(config.get("workspace_id") or "")
    channel = str(config.get("channel") or "")
    if not (_SLACK_ID.fullmatch(workspace) and _SLACK_ID.fullmatch(channel)):
        return None
    return f"https://app.slack.com/client/{workspace}/{channel}"


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
    google: IntegrationGoogle,
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


def _team_for(
    session: Session, user_id: str, *, meeting_id: str | None, team_id: str | None
) -> str:
    """The team a team-integration request is about, after checking the person
    belongs to it: named by a meeting the screen shows (the 액션 tab) or by the
    team itself (S28 settings, #496). Any member may connect -- ``team_members``
    has no admin role yet (#592)."""
    if meeting_id:
        return _team_of(session, user_id, meeting_id)
    if not team_id:
        raise ValidationError("meeting_id or team_id is required", field="team_id")
    member = session.scalar(
        select(TeamMember.user_id).where(
            TeamMember.team_id == team_id, TeamMember.user_id == user_id
        )
    )
    if member is None:
        raise PermissionDeniedError("not a member of this team")
    return team_id


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
    meeting_id: Annotated[str | None, Query()] = None,
    team_id: Annotated[str | None, Query()] = None,
    redirect_to: Annotated[str, Query()] = "/",
) -> RedirectResponse:
    """Send a team member to Atlassian to connect the team's Jira.

    The grant is theirs (#82): it lasts while their Atlassian account does, and
    the connection says who made it (``connected_by``). Same browser-bound
    ``state`` as Google sign-in, cookie scoped to the Jira callback."""
    team_id = _team_for(session, user.id, meeting_id=meeting_id, team_id=team_id)
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
    meeting_id: Annotated[str | None, Query()] = None,
    team_id: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    """The team's Jira connection as a member sees it: which site, which project,
    and whether it needs someone to reconnect. With no project chosen yet, the
    projects to choose from."""
    team_id = _team_for(session, user.id, meeting_id=meeting_id, team_id=team_id)
    config = load_integration(session, team_id, JIRA)
    if config is None or not config.secret:
        return {"connected": False}
    answer: dict[str, object] = {
        "connected": True,
        "needs_reconnect": bool(config.config.get("needs_reconnect")),
        "site_name": config.config.get("site_name"),
        # The team's own Jira site, for a link beside the connection.
        "site_url": _https_link(config.config.get("site_url")),
        "project_key": config.config.get("project_key"),
        # The key of a chosen project that has since been deleted in Jira.
        "project_missing": config.config.get("project_missing"),
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
    project_key: Annotated[str, Query()],
    meeting_id: Annotated[str | None, Query()] = None,
    team_id: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    """Which project confirmed items become issues in. Only a project the grant
    can actually see is accepted."""
    team_id = _team_for(session, user.id, meeting_id=meeting_id, team_id=team_id)
    config = load_integration(session, team_id, JIRA)
    if config is None:
        raise NotFoundError("integration", f"jira for team {team_id}")
    if project_key not in {p.key for p in _projects_for(team_id)}:
        raise PermissionDeniedError("that project is not visible to this Jira connection")
    save_integration(
        session,
        team_id,
        JIRA,
        config={**config.config, "project_key": project_key, "project_missing": None},
    )
    return {"connected": True, "project_key": project_key}


@router.post("/jira/disconnect")
def jira_disconnect(
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    meeting_id: Annotated[str | None, Query()] = None,
    team_id: Annotated[str | None, Query()] = None,
) -> dict[str, bool]:
    """Forget the team's Jira connection. Atlassian offers no API to revoke a
    3LO grant; the person who connected removes Autune from their Atlassian
    account's connected apps -- the answer says so (``revoked: false``)."""
    team_id = _team_for(session, user.id, meeting_id=meeting_id, team_id=team_id)
    disconnect_integration(session, team_id, JIRA)
    log.info("auth_jira_disconnected", team_id=team_id, user_id=user.id)
    return {"connected": False, "revoked": False}


# --------------------------------------------------------------------------- #
# A team's Notion workspace (#428): one click, Notion's public-integration OAuth
# --------------------------------------------------------------------------- #

NOTION = "notion"


def _notion_callback_path(request: Request) -> str:
    return request.url_for("notion_callback").path


@router.get("/notion/start")
def notion_start(
    request: Request,
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    store: Annotated[StateStore, Depends(get_state_store)],
    notion: Annotated[NotionOAuthClient, Depends(get_notion_oauth_client)],
    meeting_id: Annotated[str | None, Query()] = None,
    team_id: Annotated[str | None, Query()] = None,
    redirect_to: Annotated[str, Query()] = "/",
) -> RedirectResponse:
    """Send a team member to Notion to connect the team's workspace. On Notion's
    screen they also pick the pages Autune may see -- one of those becomes the
    parent of Autune's databases (module B)."""
    team_id = _team_for(session, user.id, meeting_id=meeting_id, team_id=team_id)
    state = secrets.token_urlsafe(32)
    store.put(
        state,
        OAuthTransaction(
            nonce="",
            redirect_to=_safe_redirect_target(redirect_to),
            purpose="notion",
            user_id=user.id,
            team_id=team_id,
        ),
    )
    response = RedirectResponse(notion.authorization_url(state=state), status_code=307)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        path=_notion_callback_path(request),
    )
    return response


@router.get("/notion/callback")
def notion_callback(
    request: Request,
    state: Annotated[str, Query()],
    store: Annotated[StateStore, Depends(get_state_store)],
    notion: Annotated[NotionOAuthClient, Depends(get_notion_oauth_client)],
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
            content=PermissionDeniedError(
                "Notion connect was not started in this browser"
            ).to_dict(),
        )
    else:
        transaction = store.pop(state)
        if transaction is None or transaction.purpose != "notion" or not transaction.team_id:
            response = JSONResponse(
                status_code=403,
                content=PermissionDeniedError(
                    "Notion connect state is unknown or expired"
                ).to_dict(),
            )
        else:
            response = _finish_notion_connect(transaction, notion, session, code=code, error=error)
    response.delete_cookie(STATE_COOKIE, path=_notion_callback_path(request))
    return response


def _finish_notion_connect(
    transaction: OAuthTransaction,
    notion: NotionOAuthClient,
    session: Session,
    *,
    code: str | None,
    error: str | None,
) -> RedirectResponse:
    """Store the workspace's bot token, then back to the screen with
    ``?notion=connected`` (the screen asks module B to set up the databases) or
    ``?notion=failed``. A new connection replaces the old config whole: ids from
    another workspace would point at nothing."""
    assert transaction.team_id is not None
    try:
        if error or not code:
            raise PermissionDeniedError("Notion access was not granted")
        grant = notion.exchange_code(code)
        save_integration(
            session,
            transaction.team_id,
            NOTION,
            secret=grant.access_token,
            config={
                "workspace_id": grant.workspace_id,
                "workspace_name": grant.workspace_name,
                "bot_id": grant.bot_id,
            },
            connected_by=transaction.user_id,
        )
        log.info("auth_notion_connected", team_id=transaction.team_id)
        outcome = "connected"
    except AutuneError as exc:
        log.info("auth_notion_connect_failed", team_id=transaction.team_id, reason=exc.code)
        outcome = "failed"
    return RedirectResponse(
        _web_url(_with_query(transaction.redirect_to, f"notion={outcome}")), status_code=303
    )


@router.get("/notion")
def notion_status(
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    meeting_id: Annotated[str | None, Query()] = None,
    team_id: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    """The team's Notion connection as a member sees it. Which page and which
    databases are module B's to answer (``/api/extraction/notion/setup``)."""
    team_id = _team_for(session, user.id, meeting_id=meeting_id, team_id=team_id)
    config = load_integration(session, team_id, NOTION)
    if config is None or not config.secret:
        return {"connected": False}
    return {"connected": True, "workspace_name": config.config.get("workspace_name")}


@router.post("/notion/disconnect")
def notion_disconnect(
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    meeting_id: Annotated[str | None, Query()] = None,
    team_id: Annotated[str | None, Query()] = None,
) -> dict[str, bool]:
    """Forget the team's Notion token. The pages and databases stay in Notion --
    they are the team's -- and the person removes Autune under Notion's
    Settings > Connections to end the grant there (``revoked: false``)."""
    team_id = _team_for(session, user.id, meeting_id=meeting_id, team_id=team_id)
    disconnect_integration(session, team_id, NOTION)
    log.info("auth_notion_disconnected", team_id=team_id, user_id=user.id)
    return {"connected": False, "revoked": False}


# --------------------------------------------------------------------------- #
# A team's Slack workspace (#428): one click, "Add to Slack" (OAuth v2)
# --------------------------------------------------------------------------- #

SLACK = "slack"


def _slack_callback_path(request: Request) -> str:
    return request.url_for("slack_callback").path


@router.get("/slack/start")
def slack_start(
    request: Request,
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    store: Annotated[StateStore, Depends(get_state_store)],
    slack: Annotated[SlackOAuthClient, Depends(get_slack_oauth_client)],
    meeting_id: Annotated[str | None, Query()] = None,
    team_id: Annotated[str | None, Query()] = None,
    redirect_to: Annotated[str, Query()] = "/",
) -> RedirectResponse:
    """Send a team member to Slack to install Autune's bot in the team's
    workspace. Same browser-bound ``state`` as the other connects."""
    team_id = _team_for(session, user.id, meeting_id=meeting_id, team_id=team_id)
    state = secrets.token_urlsafe(32)
    store.put(
        state,
        OAuthTransaction(
            nonce="",
            redirect_to=_safe_redirect_target(redirect_to),
            purpose="slack",
            user_id=user.id,
            team_id=team_id,
        ),
    )
    response = RedirectResponse(slack.authorization_url(state=state), status_code=307)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        path=_slack_callback_path(request),
    )
    return response


@router.get("/slack/callback")
def slack_callback(
    request: Request,
    state: Annotated[str, Query()],
    store: Annotated[StateStore, Depends(get_state_store)],
    slack: Annotated[SlackOAuthClient, Depends(get_slack_oauth_client)],
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
            content=PermissionDeniedError(
                "Slack connect was not started in this browser"
            ).to_dict(),
        )
    else:
        transaction = store.pop(state)
        if transaction is not None and transaction.purpose == "slack_identity":
            response = _finish_slack_identity(
                request, transaction, slack, session, code=code, error=error
            )
        elif transaction is None or transaction.purpose != "slack" or not transaction.team_id:
            response = JSONResponse(
                status_code=403,
                content=PermissionDeniedError(
                    "Slack connect state is unknown or expired"
                ).to_dict(),
            )
        else:
            response = _finish_slack_connect(transaction, slack, session, code=code, error=error)
    response.delete_cookie(STATE_COOKIE, path=_slack_callback_path(request))
    return response


def _finish_slack_connect(
    transaction: OAuthTransaction,
    slack: SlackOAuthClient,
    session: Session,
    *,
    code: str | None,
    error: str | None,
) -> RedirectResponse:
    """Store the workspace bot's token and the private alert channel the install
    made -- the ``channel`` key D and E already read -- then back to the screen
    with ``?slack=connected|failed``.

    Tokens are never left alive and unknown to us: one issued by an install that
    then fails is revoked, and so is the one a re-install to another workspace
    replaces -- otherwise that bot stays in the old workspace and not even
    ``/slack/disconnect`` could end it.

    Two things stop a revoke. A token equal to the stored one: re-installing
    into the same workspace can hand the same token back. And **another team on
    the same workspace**: Slack issues one bot token per app and workspace, so
    every Autune team installed there holds that token, and revoking it for one
    would silence the others (review of #468)."""
    assert transaction.team_id is not None
    team_id = transaction.team_id
    install: SlackInstall | None = None
    previous: IntegrationConfig | None = None
    made: SlackChannel | None = None
    shared = False
    try:
        if error or not code:
            raise PermissionDeniedError("Slack install was not approved")
        # Before Slack is touched: a deploy that cannot store the token fails
        # here, not after a channel it would leave behind (#593).
        ensure_configured()
        install = slack.exchange_code(code)
        previous = load_integration(session, team_id, SLACK)
        # Decided before anything else can fail, so a failure path never has
        # to ask a session that may be broken.
        shared = _workspace_used_elsewhere(session, install.workspace_id, team_id)
        channel = _alert_channel(slack, install, previous)
        if previous is None or channel.id != previous.config.get("channel"):
            made = channel
        save_integration(
            session,
            transaction.team_id,
            SLACK,
            secret=install.access_token,
            config={
                "channel": channel.id,
                "channel_name": channel.name,
                "workspace_id": install.workspace_id,
                "workspace_name": install.workspace_name,
                "bot_user_id": install.bot_user_id,
            },
            connected_by=transaction.user_id,
        )
        if (
            previous is not None
            and previous.secret
            and previous.secret != install.access_token
            and not _workspace_used_elsewhere(
                session, str(previous.config.get("workspace_id") or ""), team_id
            )
        ):
            slack.revoke(previous.secret)
        log.info("auth_slack_connected", team_id=team_id)
        outcome = "connected"
    except Exception as exc:
        if install is not None and made is not None:
            # With the new token, before it is revoked: after, nothing could
            # archive the channel this attempt made (#593). Best effort; the
            # original failure is what the person is told.
            slack.discard_channel(install.access_token, made.id)
            log.info("auth_slack_channel_discarded", team_id=team_id, channel=made.id)
        if (
            install is not None
            and not shared
            and install.access_token != (previous.secret if previous else None)
        ):
            slack.revoke(install.access_token)
        if not isinstance(exc, AutuneError):
            raise
        log.info("auth_slack_connect_failed", team_id=team_id, reason=exc.code)
        # Our own error code, never Slack's text: the screen explains the one
        # case a person can fix (a private #autune) and is generic otherwise.
        outcome = f"failed&reason={exc.code}"
    return RedirectResponse(
        _web_url(_with_query(transaction.redirect_to, f"slack={outcome}")), status_code=303
    )


def _alert_channel(
    slack: SlackOAuthClient, install: SlackInstall, previous: IntegrationConfig | None
) -> SlackChannel:
    """The team's channel: kept on a re-install into the same workspace while it
    can still take posts, made new (private, installer invited) otherwise."""
    if previous is not None and previous.config.get("workspace_id") == install.workspace_id:
        kept = previous.config.get("channel")
        if kept and slack.channel_usable(install.access_token, str(kept)):
            return SlackChannel(str(kept), str(previous.config.get("channel_name") or ""))
    return slack.create_alert_channel(
        install.access_token, get_settings().slack_channel_name, invite=install.installer_id
    )


def _workspace_used_elsewhere(session: Session, workspace_id: str, team_id: str) -> bool:
    """Another Autune team is installed on this Slack workspace -- and so holds
    the same bot token."""
    if not workspace_id:
        return False
    return any(
        other != team_id for other in teams_with(session, SLACK, "workspace_id", workspace_id)
    )


@router.get("/slack")
def slack_status(
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    meeting_id: Annotated[str | None, Query()] = None,
    team_id: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    team_id = _team_for(session, user.id, meeting_id=meeting_id, team_id=team_id)
    config = load_integration(session, team_id, SLACK)
    if config is None or not config.secret:
        return {"connected": False}
    return {
        "connected": True,
        "workspace_name": config.config.get("workspace_name"),
        "channel_name": config.config.get("channel_name"),
        # The alert channel in the team's own workspace, for a link beside it.
        "channel_url": _slack_channel_link(config.config),
    }


@router.post("/slack/disconnect")
def slack_disconnect(
    user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    slack: Annotated[SlackOAuthClient, Depends(get_slack_oauth_client)],
    meeting_id: Annotated[str | None, Query()] = None,
    team_id: Annotated[str | None, Query()] = None,
) -> dict[str, bool]:
    """Revoke the bot token at Slack (``auth.revoke``), then forget it. Our copy
    goes even when Slack does not answer; ``revoked`` says which. When another
    team on the same workspace still uses the bot, the token is left alive for
    it and ``shared`` says so."""
    team_id = _team_for(session, user.id, meeting_id=meeting_id, team_id=team_id)
    config = load_integration(session, team_id, SLACK)
    shared = config is not None and _workspace_used_elsewhere(
        session, str(config.config.get("workspace_id") or ""), team_id
    )
    revoked = bool(config and config.secret and not shared and slack.revoke(config.secret))
    disconnect_integration(session, team_id, SLACK)
    log.info(
        "auth_slack_disconnected", team_id=team_id, user_id=user.id, revoked=revoked, shared=shared
    )
    return {"connected": False, "revoked": revoked, "shared": shared}


# --------------------------------------------------------------------------- #
# A person's own Slack account, for direct messages (#255, #280)
# --------------------------------------------------------------------------- #


@router.get("/slack/me/start")
def slack_identity_start(
    request: Request,
    user: CurrentUser,
    store: Annotated[StateStore, Depends(get_state_store)],
    slack: Annotated[SlackOAuthClient, Depends(get_slack_oauth_client)],
    redirect_to: Annotated[str, Query()] = "/",
) -> RedirectResponse:
    """ "Sign in with Slack" so direct messages can reach this person. Only their
    own member id comes back -- no email, no directory (#70). The shared Slack
    callback finishes it; the id is stored for the person of *this* session."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    store.put(
        state,
        OAuthTransaction(
            nonce=nonce,
            redirect_to=_safe_redirect_target(redirect_to),
            purpose="slack_identity",
            user_id=user.id,
        ),
    )
    response = RedirectResponse(slack.identity_url(state=state, nonce=nonce), status_code=307)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        path=_slack_callback_path(request),
    )
    return response


SLACK_CONFIRM_TTL = timedelta(minutes=30)
"""How long the confirmation link a new Slack link waits on stays good."""

_PENDING_KEYS = (
    "pending_slack_user_id",
    "pending_slack_team_id",
    "confirm_digest",
    "confirm_expires_at",
    "confirm_redirect_to",
)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _finish_slack_identity(
    request: Request,
    transaction: OAuthTransaction,
    slack: SlackOAuthClient,
    session: Session,
    *,
    code: str | None,
    error: str | None,
) -> RedirectResponse:
    """Hold the Slack account the browser signed in with as *pending*, and ask
    that account to confirm (#478 review).

    The browser may carry someone else's Slack session -- a shared computer,
    before that person ever linked theirs -- and nothing in the sign-in says
    whose it is. So the team's bot DMs a one-time link to the member id that
    came back, and only opening it in *this* Autune session makes the link
    real. The owner of a leftover session gets a link they cannot use; the
    person who started never sees it. Until then ``slack_member_id`` answers
    ``None`` and no DM, speaking ratio included, goes to that account. An
    earlier confirmed link keeps working while a new one waits."""
    try:
        if error or not code or not transaction.user_id:
            raise PermissionDeniedError("Slack sign-in was not approved")
        identity = slack.identify(code, nonce=transaction.nonce)
        workspaces = _slack_workspaces_of(session, transaction.user_id)
        if not workspaces:
            raise SlackTeamNotConnectedError("no team of this person has installed Autune in Slack")
        if identity.team_id not in workspaces:
            raise SlackWrongWorkspaceError("signed in to a workspace no team of theirs installed")
        others = [
            uid
            for uid in users_linked_to_slack_member(session, identity.user_id)
            if uid != transaction.user_id
        ]
        if others:
            raise SlackAccountTakenError("that Slack account is linked to another person")
        bot = _slack_bot_for_workspace(session, transaction.user_id, identity.team_id)
        token = secrets.token_urlsafe(32)
        # The web origin, like every other address this file hands a browser:
        # the Host the API saw is the proxy's target, which a person's browser
        # may not reach and where the session cookie is not sent (#478 review).
        path = request.url_for("slack_identity_confirm").path
        link = _web_url(f"{path}?token={token}")
        slack.send_link_confirmation(
            bot,
            identity.user_id,
            "Autune에서 이 Slack 계정으로 개인 알림을 받겠다는 연결 요청이 왔습니다. "
            f"이 Slack 계정의 주인 본인이 요청한 경우에만 이 링크를 여세요: {link}\n"
            "Autune에 로그인한 브라우저에서 열어야 합니다. Slack 앱에서 누르면 다른 "
            "브라우저가 열릴 수 있으니, 그때는 링크를 복사해 그 브라우저에 붙여 넣으세요.\n"
            "요청한 적이 없다면 열지 말고 무시하세요. 다른 사람이 이 브라우저에 남은 "
            "Slack 로그인으로 연결을 시도한 것일 수 있습니다. 30분 뒤 만료됩니다.",
        )
        existing = load_user_integration(session, transaction.user_id, "slack")
        kept = {
            k: v
            for k, v in (existing.config.items() if existing is not None else [])
            if k in ("slack_user_id", "slack_team_id")
        }
        save_user_integration(
            session,
            transaction.user_id,
            "slack",
            config={
                **kept,
                "pending_slack_user_id": identity.user_id,
                "pending_slack_team_id": identity.team_id,
                "confirm_digest": _digest(token),
                "confirm_expires_at": (datetime.now(UTC) + SLACK_CONFIRM_TTL).isoformat(),
                "confirm_redirect_to": transaction.redirect_to,
            },
        )
        log.info("auth_slack_identity_pending", user_id=transaction.user_id)
        outcome = "pending"
    except AutuneError as exc:
        log.info("auth_slack_identity_failed", user_id=transaction.user_id, reason=exc.code)
        outcome = f"failed&reason={exc.code}"
    return RedirectResponse(
        _web_url(_with_query(transaction.redirect_to, f"slack_me={outcome}")), status_code=303
    )


def _slack_bot_for_workspace(session: Session, user_id: str, workspace: str) -> str:
    """The bot token of a team of this person installed in ``workspace`` --
    the bot that will DM them, so the one to ask them to confirm."""
    team_ids = session.scalars(select(TeamMember.team_id).where(TeamMember.user_id == user_id))
    for team_id in team_ids:
        installed = load_integration(session, team_id, SLACK)
        if (
            installed is not None
            and installed.secret
            and installed.config.get("workspace_id") == workspace
        ):
            return installed.secret
    raise SlackWrongWorkspaceError("signed in to a workspace no team of theirs installed")


SLACK_CONFIRM_NEEDS_SESSION = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>Autune</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: sans-serif; max-width: 32rem; margin: 3rem auto; padding: 0 1rem">
<h1 style="font-size: 1.25rem">이 브라우저에서는 연결을 확정할 수 없습니다</h1>
<p>이 링크는 Slack 연결을 시작한 브라우저에서, Autune에 로그인한 상태로 열어야 합니다.
Slack 앱에서 링크를 누르면 다른 브라우저가 열릴 수 있습니다.</p>
<p>Slack의 메시지에서 링크를 복사해, Autune이 열려 있는 브라우저의 주소창에 붙여 넣어 주세요.</p>
</body></html>"""
"""What a browser without an Autune session gets from the confirmation link.
Slack's desktop app opens links in the default browser, which is often not the
one signed in to Autune; a JSON 403 there told a person nothing (found clicking
through with real Slack, #478)."""


@router.get("/slack/me/confirm", name="slack_identity_confirm", response_model=None)
def slack_identity_confirm(
    request: Request,
    token: Annotated[str, Query()],
    session: Annotated[Session, Depends(get_session)],
) -> RedirectResponse | HTMLResponse:
    """The link the bot DMed. Confirms the pending Slack account only for the
    Autune person whose connect is pending -- their session, their digest, in
    time (#478 review). Anyone else, a second use, or a late one changes
    nothing. A browser with no session gets a page saying where to open it."""
    try:
        user = current_user(
            _session_token(
                request.headers.get("authorization"), request.cookies.get(SESSION_COOKIE)
            ),
            session,
        )
    except AutuneError:
        return HTMLResponse(SLACK_CONFIRM_NEEDS_SESSION, status_code=401)
    linked = load_user_integration(session, user.id, "slack")
    config = dict(linked.config) if linked is not None else {}
    redirect_to = str(config.get("confirm_redirect_to") or "/")
    try:
        pending = config.get("pending_slack_user_id")
        expires = config.get("confirm_expires_at")
        digest = str(config.get("confirm_digest") or "")
        if (
            not pending
            or not expires
            or datetime.fromisoformat(str(expires)) <= datetime.now(UTC)
            or not hmac.compare_digest(digest, _digest(token))
        ):
            raise SlackLinkNotConfirmedError("no pending Slack link matches this link")
        others = [u for u in users_linked_to_slack_member(session, str(pending)) if u != user.id]
        if others:
            raise SlackAccountTakenError("that Slack account is linked to another person")
        confirmed = {k: v for k, v in config.items() if k not in _PENDING_KEYS}
        confirmed["slack_user_id"] = str(pending)
        confirmed["slack_team_id"] = str(config.get("pending_slack_team_id") or "")
        try:
            save_user_integration(session, user.id, "slack", config=confirmed)
            session.flush()
        except IntegrityError:
            # Two people confirming the same Slack account at once both pass
            # the check above; the unique index lets only the first through.
            session.rollback()
            raise SlackAccountTakenError("that Slack account is linked to another person") from None
        log.info("auth_slack_identity_linked", user_id=user.id)
        outcome = "connected"
    except AutuneError as exc:
        log.info("auth_slack_identity_failed", user_id=user.id, reason=exc.code)
        outcome = f"failed&reason={exc.code}"
    return RedirectResponse(
        _web_url(_with_query(redirect_to, f"slack_me={outcome}")), status_code=303
    )


def _slack_workspaces_of(session: Session, user_id: str) -> dict[str, str]:
    """``{workspace id: name}`` for every Slack workspace a team of this
    person installed Autune in -- the only places their DMs can come from."""
    team_ids = session.scalars(select(TeamMember.team_id).where(TeamMember.user_id == user_id))
    workspaces: dict[str, str] = {}
    for team_id in team_ids:
        installed = load_integration(session, team_id, SLACK)
        workspace = str(installed.config.get("workspace_id") or "") if installed else ""
        if installed is not None and installed.secret and workspace:
            workspaces[workspace] = str(installed.config.get("workspace_name") or "")
    return workspaces


@router.get("/slack/me")
def slack_identity_status(
    user: CurrentUser, session: Annotated[Session, Depends(get_session)]
) -> dict[str, object]:
    """Whether the signed-in person linked their Slack account, and in which
    workspace -- theirs only, so a person can see a link that is not theirs."""
    linked = load_user_integration(session, user.id, "slack")
    if linked is None or not linked.config.get("slack_user_id"):
        # A link waiting for its confirmation DM is not a link yet -- and one
        # past its 30 minutes is not waiting any more, so the screen offers to
        # start again instead of "check your DM" (mkkim68, review of #478).
        config = linked.config if linked is not None else {}
        expires = config.get("confirm_expires_at")
        waiting = bool(config.get("pending_slack_user_id")) and bool(
            expires and datetime.fromisoformat(str(expires)) > datetime.now(UTC)
        )
        return {"linked": False, "pending": waiting}
    workspace = str(linked.config.get("slack_team_id") or "")
    return {
        "linked": True,
        "workspace_name": _slack_workspaces_of(session, user.id).get(workspace),
    }


@router.post("/slack/me/disconnect")
def slack_identity_disconnect(
    user: CurrentUser, session: Annotated[Session, Depends(get_session)]
) -> dict[str, bool]:
    """Forget the person's Slack id. No token was kept, so nothing to revoke."""
    disconnect_user_integration(session, user.id, "slack")
    return {"linked": False}
