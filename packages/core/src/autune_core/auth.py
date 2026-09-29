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
    payload = {"sub": user_id, "iat": now, "exp": now + ttl}
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


def current_user(
    token: Annotated[str, Depends(_session_token)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    """FastAPI dependency resolving the authenticated user."""
    user_id = decode_token(token).get("sub")
    if not user_id:
        raise PermissionDeniedError("token carries no subject")
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError("user", user_id)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_self(requester: User, subject_id: str) -> None:
    """Authorize an endpoint that may only ever serve the subject themselves.

    Used for speaking ratios (S23). There is deliberately no admin override:
    nobody but the speaker may see their own share of a meeting, including team
    administrators. See docs/architecture/privacy.md section 3.
    """
    if requester.id != subject_id:
        raise PermissionDeniedError("this data is available only to the person it describes")
