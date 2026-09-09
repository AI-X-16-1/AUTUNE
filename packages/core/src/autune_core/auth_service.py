"""Turning a verified external identity into a ``User`` row.

The auth layer is the only writer of ``users`` (see docs/architecture/data-model.md);
modules read them. First Google sign-in creates the row, a later one updates the
profile, and a Google login that matches an email a magic link already created
links the two by filling in ``google_sub``.

Team membership is not decided here — a brand-new user has no ``team_members``
row until they are invited or create a team, which is a separate flow.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .entities import User
from .ids import USER, new_id
from .oauth.google import GoogleIdentity


def upsert_user_from_google(session: Session, identity: GoogleIdentity) -> User:
    user = session.scalar(select(User).where(User.google_sub == identity.sub))

    if user is None:
        # A magic-link user may already hold this email; adopt that row.
        user = session.scalar(select(User).where(User.email == identity.email))
        if user is not None:
            user.google_sub = identity.sub

    if user is None:
        user = User(
            id=new_id(USER),
            email=identity.email,
            display_name=identity.name or identity.email,
            google_sub=identity.sub,
        )
        session.add(user)
    else:
        user.email = identity.email
        if identity.name:
            user.display_name = identity.name

    user.last_login_at = datetime.now(UTC)
    session.flush()
    return user
