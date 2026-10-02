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
(``users.sessions_valid_from``) and ``current_user`` refuses any token issued
before it -- cookie or bearer, a developer token included.

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
    response.delete_cookie(SESSION_COOKIE, path="/")


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


def _issued_before_sign_out(user: User, claims: dict[str, Any]) -> bool:
    cutoff = user.sessions_valid_from
    if cutoff is None:
        return False
    if cutoff.tzinfo is None:  # SQLite hands a timezone-aware column back naive
        cutoff = cutoff.replace(tzinfo=UTC)
    # A token with no ``iat`` cannot be placed after the sign-out, so it is
    # before it. An older token's ``iat`` is a whole second, rounded down,
    # which errs the same way.
    issued = claims.get("iat")
    return not isinstance(issued, int | float) or issued < cutoff.timestamp()


def current_user(
    token: Annotated[str, Depends(_session_token)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    """FastAPI dependency resolving the authenticated user."""
    claims = decode_token(token)
    user_id = claims.get("sub")
    if not user_id:
        raise PermissionDeniedError("token carries no subject")
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError("user", user_id)
    if _issued_before_sign_out(user, claims):
        raise PermissionDeniedError("this session was signed out; sign in again")
    return user


def end_sessions(user: User, *, now: datetime | None = None) -> None:
    """Sign the person out everywhere: no token issued before this moment is
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
