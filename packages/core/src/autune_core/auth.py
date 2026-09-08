"""Authentication skeleton.

W1 scope: issue and verify a JWT, and resolve the current user from it. Real
Google, Slack and magic-link sign-in (screen S01) arrives in W2 — the point of
this file is that module routers can depend on ``current_user`` today and not be
rewritten when real sign-in lands.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header
from sqlalchemy.orm import Session

from .db import get_session
from .entities import User
from .errors import NotFoundError, PermissionDeniedError
from .settings import get_settings

ALGORITHM = "HS256"
DEFAULT_TTL = timedelta(days=7)


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


def _bearer(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise PermissionDeniedError("missing bearer token")
    return authorization.split(" ", 1)[1]


def current_user(
    token: Annotated[str, Depends(_bearer)],
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
