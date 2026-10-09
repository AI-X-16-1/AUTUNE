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
- ``POST /logout``         -> end every session the person has, so no token
                              issued so far is accepted again, then clear the
                              cookie (see environments.md)
- ``GET /me``              -> the current user (used by the web app to bootstrap)
- ``GET /google/calendar/start``       -> Google's consent for the person's own
                                         calendar, offline; the same callback
                                         finishes it (purpose ``calendar``)
- ``GET /google/calendar``             -> whether *this* person connected one
- ``POST /google/calendar/disconnect`` -> revoke at Google, then forget
- ``GET /google/gmail/start``, ``GET /google/gmail``,
  ``POST /google/gmail/disconnect``    -> the same three for sending mail as
                                         the person (``gmail.send``, #552)
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, Header, Query, Request
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
    end_sessions,
    issue_token,
    set_session_cookie,
    signed_in_user_or_none,
)
from .auth_service import upsert_user_from_google
from .consents import Consent, consents_of, record_consents
from .crypto import ensure_configured
from .db import SessionDep
from .entities import Meeting, Team, TeamMember, User, team_order
from .errors import (
    AutuneError,
    ConfigurationError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
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
    GMAIL_SEND_SCOPE,
    GoogleOAuthClient,
    get_google_client,
    get_google_integration_client,
    pkce_pair,
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
    channel_name_for,
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
    integration: Annotated[GoogleOAuthClient | None, Depends(get_google_integration_client)],
) -> GoogleOAuthClient:
    """The Google client a person's own grant goes through -- calendar today.
    The deployment's integration client when it has one, the sign-in client
    otherwise. Whichever starts a connect has to finish it: the code Google
    returns can only be exchanged by the client it was issued to, and the ID
    token's ``aud`` names that client."""
    return integration or sign_in


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
    verifier, challenge = pkce_pair()
    store.put(
        state,
        OAuthTransaction(
            nonce=nonce, redirect_to=_safe_redirect_target(redirect_to), code_verifier=verifier
        ),
    )
    response = RedirectResponse(
        google.authorization_url(state=state, nonce=nonce, code_challenge=challenge),
        status_code=307,
    )
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
    session: SessionDep,
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

    if transaction.purpose in _PERSONAL:
        # Finished by the client that started it (``integration_google``).
        return _finish_personal_connect(
            transaction, integration or google, session, code=code, error=error
        )
    if transaction.purpose != "sign_in":
        # Other flows (Jira, Notion, Slack) share this store; their state is
        # never a Google sign-in, even with the cookie and query both set.
        raise PermissionDeniedError("this state did not start a Google sign-in")

    if error or not code:
        raise PermissionDeniedError("Google sign-in did not complete")

    identity = google.verify(
        google.exchange_code(code, code_verifier=transaction.code_verifier),
        nonce=transaction.nonce,
    )
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
def logout(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
    autune_session: Annotated[str | None, Cookie()] = None,
) -> Response:
    """Sign out: end every session this person has, then clear the cookie.

    Until now this only cleared the cookie, and the token it held stayed
    valid for the rest of its seven days. ``end_sessions`` makes the server
    refuse it, and every other token the person holds -- another browser, a
    developer token, a copy that leaked.

    204 with or without a session. A request with none, an expired one or
    one already signed out has nothing to end and still gets its cookie
    cleared: a session that is missing or over is no reason for signing out
    to fail.

    What can fail is the write. ``end_sessions`` is committed by the route's
    session as this function returns (``SessionDep``, #1041), and a commit
    that fails is a 500 with the cookie left in place -- where a 204 once went
    out over tokens the server still accepted. The person is told, and signs
    out again."""
    user = signed_in_user_or_none(session, authorization, autune_session)
    if user is not None:
        end_sessions(user)
        log.info("auth_signed_out", user_id=user.id)
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/me")
def me(user: CurrentUser, session: SessionDep) -> dict[str, object]:
    """Who is signed in, and the teams they belong to -- what S28 settings
    (#496) chooses a team's integrations from, with no meeting to name it.

    **Pinned teams first, then the order the person joined them**
    (``team_order``; #742). Two screens take the
    first as the default: the assistant (S34) asks about ``teams[0]``, and
    S28 opens on it. By name, accepting an invitation (#552) to a team whose
    name sorts earlier silently made the inviting team that default -- the
    change of default nobody asked for that the review of #539 was about.
    Module A's ``teams_for`` answers in the same order for the same reason,
    so the two lists a browser holds agree on which team is first."""
    teams = session.execute(
        select(Team.id, Team.name)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user.id)
        .order_by(*team_order())
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
def my_consents(user: CurrentUser, session: SessionDep) -> dict[str, object]:
    """The documents and versions the signed-in person agreed to -- theirs
    only. The consent page compares this with what it requires; the server
    does not know which version is current (``autune_core.consents``)."""
    return _consents_answer(consents_of(session, user.id))


@router.post("/consents")
def agree(
    body: _ConsentsIn,
    user: CurrentUser,
    session: SessionDep,
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
# A person's own Google grants: their calendar (#435), sending mail as them
# (#552). One click each, their own grant, the sign-in flow's callback.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _PersonalGoogle:
    """One thing a signed-in person can let Autune do with their own Google
    account. Each is its own consent, its own refresh token carrying only its
    own scope (no ``include_granted_scopes``), and its own ``user_integrations``
    row, so connecting one never widens what another can do.

    **Revoking is not that separate.** Google's revoke can end everything one
    account gave this OAuth client, so revoking one kind's token may end the
    other's too. When that happens the other row is marked ``grant_revoked``
    (``_mark_shared_grants``) and reported as needing a reconnect, rather than
    left looking connected while nothing works (lsh2217, review of #760)."""

    service: str
    """The ``user_integrations`` service, and the transaction's ``purpose``."""
    scope: str
    label: str
    """How refusals name it: "calendar", "Gmail"."""
    query: str
    """The key the screen reads on return: ``?<query>=connected|failed``."""
    config: tuple[tuple[str, str], ...] = ()
    """Settings stored beside the grant, besides the account and client."""


CALENDAR = _PersonalGoogle(
    "calendar", CALENDAR_SCOPE, "calendar", "calendar", (("calendar_id", "primary"),)
)
GMAIL_SEND = _PersonalGoogle("gmail_send", GMAIL_SEND_SCOPE, "Gmail", "gmail")
_PERSONAL = {kind.service: kind for kind in (CALENDAR, GMAIL_SEND)}


def _mark_shared_grants(
    session: Session, user_id: str, *, revoked: _PersonalGoogle, account: object, client: str
) -> None:
    """After a revoke of ``revoked``'s token for Google account ``account`` at
    ``client``: mark the person's other grants from the same account and client
    as gone with it (class docstring). A grant recorded without its account or
    client is left as it is -- nothing says it shared anything."""
    for kind in _PERSONAL.values():
        if kind is revoked:
            continue
        other = load_user_integration(session, user_id, kind.service)
        if (
            other is None
            or not other.secret
            or other.config.get("google_sub") != account
            or other.config.get("client_id") != client
        ):
            continue
        save_user_integration(
            session,
            user_id,
            kind.service,
            secret=other.secret,
            config={**other.config, "grant_revoked": True},
        )
        log.info(f"auth_google_{kind.service}_revoked_with_another", user_id=user_id)


def _start_personal_connect(
    kind: _PersonalGoogle,
    request: Request,
    user: User,
    store: StateStore,
    google: GoogleOAuthClient,
    redirect_to: str,
) -> RedirectResponse:
    """The same flow and callback as sign-in -- the same ``state`` cookie
    binding, so a callback from another browser is refused -- with the person's
    id kept in the transaction from *this* request's session. The callback
    therefore stores the grant for whoever started, never for whoever finishes.
    """
    if (refused := _cannot_store(redirect_to, f"{kind.query}=failed")) is not None:
        return refused
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    store.put(
        state,
        OAuthTransaction(
            nonce=nonce,
            redirect_to=_safe_redirect_target(redirect_to),
            purpose=kind.service,
            user_id=user.id,
            code_verifier=verifier,
        ),
    )
    url = google.authorization_url(
        state=state,
        nonce=nonce,
        scope=f"openid {kind.scope}",
        offline=True,
        code_challenge=challenge,
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


@router.get("/google/calendar/start")
def google_calendar_start(
    request: Request,
    user: CurrentUser,
    store: Annotated[StateStore, Depends(get_state_store)],
    google: IntegrationGoogle,
    redirect_to: Annotated[str, Query()] = "/",
) -> RedirectResponse:
    """Send a signed-in person to Google to let Autune put their own tasks'
    due dates on their own calendar."""
    return _start_personal_connect(CALENDAR, request, user, store, google, redirect_to)


@router.get("/google/gmail/start")
def google_gmail_start(
    request: Request,
    user: CurrentUser,
    store: Annotated[StateStore, Depends(get_state_store)],
    google: IntegrationGoogle,
    redirect_to: Annotated[str, Query()] = "/",
) -> RedirectResponse:
    """Send a signed-in person to Google to let Autune send mail as them --
    a team invitation from their own address (#552). ``gmail.send`` only:
    nothing in their mailbox can be read with it."""
    return _start_personal_connect(GMAIL_SEND, request, user, store, google, redirect_to)


def _finish_personal_connect(
    transaction: OAuthTransaction,
    google: GoogleOAuthClient,
    session: Session,
    *,
    code: str | None,
    error: str | None,
) -> RedirectResponse:
    """A connect that fails goes back to the screen it started from with
    ``?<query>=failed``, not to a JSON error: the person pressed a button on
    that screen and is still signed in there. Declining on Google's screen,
    unticking the permission's box, or Google withholding a refresh token all
    land here. Sign-in's own failures are unchanged."""
    kind = _PERSONAL[transaction.purpose]
    try:
        if error or not code:
            raise PermissionDeniedError(f"Google {kind.label} access was not granted")
        return _complete_personal_connect(kind, transaction, google, session, code=code)
    except AutuneError as exc:
        log.info(
            f"auth_google_{kind.service}_connect_failed",
            user_id=transaction.user_id,
            reason=exc.code,
        )
        return RedirectResponse(
            _web_url(_with_query(transaction.redirect_to, f"{kind.query}=failed")),
            status_code=303,
        )


def _with_query(path: str, pair: str) -> str:
    return path + ("&" if "?" in path else "?") + pair


def _cannot_store(redirect_to: str, pair: str) -> RedirectResponse | None:
    """Back to the screen with ``pair`` when this deploy could not store the
    token a connect would bring back, else ``None`` -- checked at start, so
    nobody goes through a consent screen that cannot succeed (mkkim68, review
    of #765). The callback checks again before it spends the code: the key
    can go between the two."""
    try:
        ensure_configured()
    except ConfigurationError as exc:
        log.info("auth_connect_not_started", reason=exc.code)
        return RedirectResponse(
            _web_url(_with_query(_safe_redirect_target(redirect_to), pair)), status_code=303
        )
    return None


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


def _complete_personal_connect(
    kind: _PersonalGoogle,
    transaction: OAuthTransaction,
    google: GoogleOAuthClient,
    session: Session,
    *,
    code: str,
) -> RedirectResponse:
    if not transaction.user_id:
        raise PermissionDeniedError(f"{kind.label} connect was not started by a signed-in person")
    # Before the code is spent: a deploy that cannot store the refresh token
    # fails here, not after Google has issued a grant nothing keeps (#704).
    # The same for every personal grant -- a Gmail one too (#760 review).
    ensure_configured()
    grant = google.exchange_grant(code, code_verifier=transaction.code_verifier)
    # The ID token proves this code answered *our* request (nonce), not which
    # Google account it was: someone may keep their calendar on another account,
    # and the consent asks for no ``email``, so none is required (#452 review).
    claims = google.verify_request(grant.id_token, nonce=transaction.nonce)
    account = str(claims["sub"])
    if kind.scope not in grant.scopes:
        raise PermissionDeniedError(f"{kind.label} access was not granted")
    # A token carrying another personal grant's scope is a merged one --
    # ``include_granted_scopes`` is no longer asked for, but a consent screen
    # can still hand one back -- and storing it under this kind would let it
    # do the other's job (mkkim68, review of #760). Refused; connecting again
    # is the fix.
    if any(other.scope in grant.scopes for other in _PERSONAL.values() if other is not kind):
        log.warning(f"auth_google_{kind.service}_merged_grant_refused")
        raise PermissionDeniedError(f"{kind.label} access came with another grant's scope")
    if not grant.refresh_token:
        raise PermissionDeniedError("Google granted no offline access; connect again")
    previous = load_user_integration(session, transaction.user_id, kind.service)
    previous_account = previous.config.get("google_sub") if previous is not None else None
    # Moved to another Google account: end the grant it replaces rather than
    # leave it valid and unknown to us. Never for the same account -- Google's
    # revoke ends everything that account granted Autune, the refresh token
    # just received included, and ``prompt=consent`` hands out a new token on
    # every connect, so comparing tokens cannot tell the two apart (#452
    # review). A grant saved before the account was recorded is left alone for
    # the same reason.
    if (
        previous is not None
        and previous.secret
        and previous_account is not None
        and previous_account != account
        and google.revoke(previous.secret)
    ):
        _mark_shared_grants(
            session,
            transaction.user_id,
            revoked=kind,
            account=previous_account,
            client=str(previous.config.get("client_id") or ""),
        )
    save_user_integration(
        session,
        transaction.user_id,
        kind.service,
        secret=grant.refresh_token,
        # ``sub`` is Google's stable account id, not a credential; it is kept
        # only so the next connect can tell a new account from the same one.
        #
        # ``client_id`` is the client the grant was issued to, also not a
        # credential. A refresh token works only with that client, so once the
        # deployment's integration client changes, this is how the grant is
        # known to need connecting again without a refused call to Google
        # (mkkim68, review of #700).
        config={**dict(kind.config), "google_sub": account, "client_id": google.client_id},
    )
    log.info(f"auth_google_{kind.service}_connected", user_id=transaction.user_id)
    return RedirectResponse(
        _web_url(_with_query(transaction.redirect_to, f"{kind.query}=connected")),
        status_code=303,
    )


def _personal_status(kind: _PersonalGoogle, session: Session, user: User) -> dict[str, bool]:
    """Whether the signed-in person has connected this grant -- theirs only;
    there is no way to ask about anyone else.

    ``needs_reconnect`` is true for a grant recorded as issued to another
    Google client than the one this deployment refreshes with now: its
    refresh token cannot work. A grant from before the client was recorded
    says nothing either way, and is reported as it always was. It is also
    true for a grant that went with a revoke of the person's other one
    (``grant_revoked``, ``_mark_shared_grants``).

    With no Google client configured at all there is nothing to compare
    with: nobody's grant can be refreshed in that state and the modules say
    nothing (``autune_extraction.tasks._calendars``), and the card must not say
    more than they do -- connecting again would not help. So that is not
    ``needs_reconnect`` (mminjae97, review of #711)."""
    grant = load_user_integration(session, user.id, kind.service)
    connected = grant is not None and bool(grant.secret)
    issued_to = grant.config.get("client_id") if grant is not None else None
    # "Configured" as module B reads it (``tasks._google_client_configured``):
    # an id and a secret. Half a client refreshes nothing, so it is not a
    # client to reconnect to either (review of #718).
    current, secret = get_settings().google_integration_credentials
    configured = bool(current and secret)
    revoked = grant is not None and bool(grant.config.get("grant_revoked"))
    return {
        "connected": connected,
        "needs_reconnect": connected
        and (revoked or (configured and bool(issued_to) and issued_to != current)),
    }


def _personal_disconnect(
    kind: _PersonalGoogle, session: Session, user: User, google: GoogleOAuthClient
) -> dict[str, bool]:
    """Revoke the grant at Google, then forget it here (#444 review). Our copy
    goes even when Google cannot be reached; ``revoked`` says whether Google
    confirmed, so a person knows to check their Google account otherwise.

    Google's revoke can end everything that account gave this OAuth client:
    the person's other personal grant through it, and sign-in's consent where
    it is the **sign-in client** (a deployment with no integration client) --
    the next sign-in then shows Google's consent screen again. A confirmed
    revoke therefore marks the other grant from the same account and client as
    needing a reconnect (``_mark_shared_grants``), so its card says so instead
    of looking connected while nothing works (lsh2217, review of #760)."""
    grant = load_user_integration(session, user.id, kind.service)
    revoked = bool(grant and grant.secret and google.revoke(grant.secret))
    if revoked and grant is not None:
        _mark_shared_grants(
            session,
            user.id,
            revoked=kind,
            account=grant.config.get("google_sub"),
            client=str(grant.config.get("client_id") or ""),
        )
    disconnect_user_integration(session, user.id, kind.service)
    log.info(f"auth_google_{kind.service}_disconnected", user_id=user.id, revoked=revoked)
    return {"connected": False, "revoked": revoked}


@router.get("/google/calendar")
def google_calendar_status(user: CurrentUser, session: SessionDep) -> dict[str, bool]:
    """Whether the signed-in person has connected their own calendar."""
    return _personal_status(CALENDAR, session, user)


@router.post("/google/calendar/disconnect")
def google_calendar_disconnect(
    user: CurrentUser,
    session: SessionDep,
    google: IntegrationGoogle,
) -> dict[str, bool]:
    """Revoke the calendar grant at Google, then forget it here."""
    return _personal_disconnect(CALENDAR, session, user, google)


@router.get("/google/gmail")
def google_gmail_status(user: CurrentUser, session: SessionDep) -> dict[str, bool]:
    """Whether the signed-in person has let Autune send mail as them."""
    return _personal_status(GMAIL_SEND, session, user)


@router.post("/google/gmail/disconnect")
def google_gmail_disconnect(
    user: CurrentUser,
    session: SessionDep,
    google: IntegrationGoogle,
) -> dict[str, bool]:
    """Revoke the Gmail send grant at Google, then forget it here."""
    return _personal_disconnect(GMAIL_SEND, session, user, google)


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
    session: SessionDep,
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
    if (refused := _cannot_store(redirect_to, "jira=failed")) is not None:
        return refused
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
    session: SessionDep,
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
        # Before the code is spent, as for Slack (#593, #704).
        ensure_configured()
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
    session: SessionDep,
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
    session: SessionDep,
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
    session: SessionDep,
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
    session: SessionDep,
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
    if (refused := _cannot_store(redirect_to, "notion=failed")) is not None:
        return refused
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
    session: SessionDep,
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
        # Before the code is spent, as for Slack (#593, #704).
        ensure_configured()
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
    session: SessionDep,
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
    session: SessionDep,
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
    session: SessionDep,
    store: Annotated[StateStore, Depends(get_state_store)],
    slack: Annotated[SlackOAuthClient, Depends(get_slack_oauth_client)],
    meeting_id: Annotated[str | None, Query()] = None,
    team_id: Annotated[str | None, Query()] = None,
    redirect_to: Annotated[str, Query()] = "/",
) -> RedirectResponse:
    """Send a team member to Slack to install Autune's bot in the team's
    workspace. Same browser-bound ``state`` as the other connects."""
    team_id = _team_for(session, user.id, meeting_id=meeting_id, team_id=team_id)
    if (
        refused := _cannot_store(redirect_to, "slack=failed&reason=configuration_error")
    ) is not None:
        return refused
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
    session: SessionDep,
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
        team = session.get(Team, team_id)
        channel = _alert_channel(slack, install, previous, team.name if team else "")
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
    slack: SlackOAuthClient,
    install: SlackInstall,
    previous: IntegrationConfig | None,
    team_name: str,
) -> SlackChannel:
    """The team's channel: kept on a re-install into the same workspace while it
    can still take posts, made new (private, installer invited) otherwise --
    named after the team, ``slack_channel_name`` when the team's name will not
    do (``channel_name_for``)."""
    if previous is not None and previous.config.get("workspace_id") == install.workspace_id:
        kept = previous.config.get("channel")
        if kept and slack.channel_usable(install.access_token, str(kept)):
            return SlackChannel(str(kept), str(previous.config.get("channel_name") or ""))
    fallback = get_settings().slack_channel_name
    return slack.create_alert_channel(
        install.access_token,
        channel_name_for(team_name, fallback),
        invite=install.installer_id,
        fallback=fallback,
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
    session: SessionDep,
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
    session: SessionDep,
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
    session: SessionDep,
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
def slack_identity_status(user: CurrentUser, session: SessionDep) -> dict[str, object]:
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
def slack_identity_disconnect(user: CurrentUser, session: SessionDep) -> dict[str, bool]:
    """Forget the person's Slack id. No token was kept, so nothing to revoke."""
    disconnect_user_integration(session, user.id, "slack")
    return {"linked": False}
