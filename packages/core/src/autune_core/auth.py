"""Authentication.

Two halves:

- **Sessions.** ``issue_token`` mints a short-lived HS256 JWT signed with our own
  ``secret_key``; ``current_user`` resolves it back to a ``User`` row. A module
  router depends on ``current_user`` and never learns how the session was
  established.
- **Sign-in.** The OAuth flow that produces a session lives in ``auth_router``
  and ``oauth/``. W1 shipped only the session half; Google sign-in (screen S01)
  is W2. This is the library approach — PyJWT for our own session, Google's OIDC
  verified directly, no auth BaaS.

The session travels either as ``Authorization: Bearer <jwt>`` (service clients,
tests) or as the ``autune_session`` cookie (the browser). ``current_user``
accepts both.

**Signing out ends every session the person has, on the server.** A session
is a signed token, and until 2026-10-02 signing out only cleared the
browser's cookie: the token it held stayed good for the rest of its seven
days, in that browser's history or anywhere it had leaked to. Now
``end_sessions`` writes the moment down on the person's row
(``users.sessions_valid_from``) and ``user_for_token`` refuses any token issued
up to it -- cookie or bearer, a developer token included.

**``user_for_token`` is the one place a session token becomes a person.**
``current_user`` is that function as a FastAPI dependency, and anything that
takes a token some other way -- module A's live WebSocket, whose handler
cannot use a dependency -- calls it directly. The first version of the
sign-out check lived in ``current_user`` alone, and the socket, which decoded
the token itself, went on accepting signed-out and leaked tokens (review of
#727). Do not decode a session token anywhere else.

The check is made when a request or a connection arrives. A live socket
opened before the sign-out stays open until it closes.

Every device, not the one (decided with the user): one value per person
and no session table. ``current_user`` already loads the person's row, so
the check costs no query. Nobody is signed out by the deployment itself: a
person who has never signed out has no moment to compare with, and their
older tokens stay good until they expire or the person signs out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Cookie, Depends, Header, Response
from sqlalchemy.orm import Session

from .db import get_session
from .entities import User
from .errors import NotFoundError, PermissionDeniedError
from .settings import get_settings

ALGORITHM = "HS256"
DEFAULT_TTL = timedelta(days=7)

SESSION_COOKIE = "autune_session"


def issue_token(user_id: str, ttl: timedelta = DEFAULT_TTL) -> str:
    now = datetime.now(UTC)
    # ``iat`` to the microsecond, not PyJWT's whole second for a datetime: a
    # token issued just after a sign-out has to be told from one issued just
    # before it, and a person can sign out and in again within a second.
    payload = {"sub": user_id, "iat": now.timestamp(), "exp": now + ttl}
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise PermissionDeniedError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise PermissionDeniedError("token is not valid") from exc


def set_session_cookie(response: Response, token: str, ttl: timedelta = DEFAULT_TTL) -> None:
    """Attach the session as an HttpOnly cookie. Used by the OAuth callback."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    # The attributes the cookie was set with: a browser matches a deletion to
    # the cookie by name and path, and the rest should not differ either.
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=get_settings().session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _session_token(
    authorization: Annotated[str | None, Header()] = None,
    autune_session: Annotated[str | None, Cookie()] = None,
) -> str:
    """The raw JWT, from the Authorization header if present, else the cookie."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1]
    if autune_session:
        return autune_session
    raise PermissionDeniedError("no session: send a bearer token or sign in")


def _ended_by_sign_out(user: User, claims: dict[str, Any]) -> bool:
    """Whether the person signed out at or after the moment this token was
    issued.

    **At, as well as after.** Two readings of the clock can be the same
    value -- on a clock with coarse ticks (Windows) a token issued and a
    sign-out made back to back often are -- and a token that cannot be told
    from one issued just before the sign-out is ended with it. The other
    way round, the first rule this had (strictly before), let exactly that
    token through; module A's tests caught it. The price is that a sign-in
    completed within the same tick as a sign-out gets a token that is
    already ended, and has to be made again."""
    cutoff = user.sessions_valid_from
    if cutoff is None:
        return False
    if cutoff.tzinfo is None:  # SQLite hands a timezone-aware column back naive
        cutoff = cutoff.replace(tzinfo=UTC)
    # A token with no ``iat`` cannot be placed after the sign-out, so it is
    # not after it. An older token's ``iat`` is a whole second, rounded down,
    # which errs the same way.
    issued = claims.get("iat")
    return not isinstance(issued, int | float) or issued <= cutoff.timestamp()


def user_for_token(session: Session, token: str) -> User:
    """The person a session token stands for, or the reason it stands for
    nobody. Every check a session has to pass is here and nowhere else: the
    signature and expiry, a subject, a person who still exists, and that the
    token was issued after they last signed out.

    ``PermissionDeniedError`` for a token that is not good. ``NotFoundError``
    in exactly one case -- a well-signed token whose person's row is gone --
    so a caller that must not say "not found" for that (the live socket,
    where it would read as "no such meeting") can tell it apart and say
    what it means there."""
    claims = decode_token(token)
    user_id = claims.get("sub")
    if not user_id:
        raise PermissionDeniedError("token carries no subject")
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError("user", user_id)
    if _ended_by_sign_out(user, claims):
        raise PermissionDeniedError("this session was signed out; sign in again")
    return user


def current_user(
    token: Annotated[str, Depends(_session_token)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    """FastAPI dependency resolving the authenticated user: ``user_for_token``
    for the token the request carried."""
    return user_for_token(session, token)


def end_sessions(user: User, *, now: datetime | None = None) -> None:
    """Sign the person out everywhere: no token issued up to this moment is
    accepted again, whichever browser, device or script holds it. A token
    issued after it -- the next sign-in -- is unaffected.

    The caller's transaction commits it. Never moved backwards: a second
    sign-out with an older clock must not revive what the first ended."""
    moment = now or datetime.now(UTC)
    current = user.sessions_valid_from
    if current is not None and current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if current is None or moment > current:
        user.sessions_valid_from = moment


def signed_in_user_or_none(
    session: Session, authorization: str | None, autune_session: str | None
) -> User | None:
    """Who the request is from, or ``None`` -- for the one route that must
    answer either way. Signing out with an expired, forged or already
    signed-out token is still a sign-out, and has nobody to end sessions for."""
    try:
        return current_user(_session_token(authorization, autune_session), session)
    except (PermissionDeniedError, NotFoundError):
        return None


CurrentUser = Annotated[User, Depends(current_user)]


def require_self(requester: User, subject_id: str) -> None:
    """Authorize an endpoint that may only ever serve the subject themselves.

    Used for speaking ratios (S23). There is deliberately no admin override:
    nobody but the speaker may see their own share of a meeting, including team
    administrators. See docs/architecture/privacy.md section 3.
    """
    if requester.id != subject_id:
        raise PermissionDeniedError("this data is available only to the person it describes")
