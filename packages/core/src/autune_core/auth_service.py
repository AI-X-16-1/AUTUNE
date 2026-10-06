"""Turning a verified external identity into a ``User`` row.

The auth layer is the only writer of ``users`` (see docs/architecture/data-model.md);
modules read them. First Google sign-in creates the row, a later one updates the
profile, and a Google login that matches an email a magic link already created
links the two by filling in ``google_sub``.

A Google account whose email changed to one another row already holds keeps
its old email: ``users.email`` is unique, and which of the two rows owns that
address is not something a sign-in can decide.

Team membership is not decided here — a brand-new user has no ``team_members``
row until they are invited or create a team, which is a separate flow.

**An address is compared without case** (#552). ``Kim@Example.com`` and
``kim@example.com`` are one mailbox, an invitation already treats them as one
(``autune_audio.invitations.normalise``), and a sign-in that did not would give
the same person a second account beside the row a magic link made. So every
lookup by address here ignores case, and an address this module writes -- a
new account's, or one that changed at Google -- is written trimmed and
lower-cased.

**A row that is already stored is not rewritten.** An account whose address
was stored with capitals keeps it as stored: it is found without case, and
changing an account's address is not something a sign-in does on its own. No
migration lower-cases the table either, because two rows that differ only by
case would collide on ``users.email``'s unique constraint, and which of them
is the person is not something a migration can decide. Should two such rows
exist, a sign-in takes the one stored exactly as Google sent it, else the
oldest, and says so in the log by id.

What this does not do: ``users.email`` is still unique *with* case, so the
database itself does not refuse a second row that differs only by case. That
needs a unique index on the lower-cased address and the collision question
above answered first.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .entities import User
from .ids import USER, new_id
from .logging import get_logger
from .oauth.google import GoogleIdentity

log = get_logger(__name__)


def normalise_email(email: str) -> str:
    """An address as it is compared and as a new one is stored: trimmed, lower-cased."""
    return email.strip().lower()


def _holder(session: Session, address: str, *, as_sent: str) -> User | None:
    """The account that holds ``address``, compared without case, or ``None``.

    More than one row can match only when two were stored differing by case
    alone, which the unique constraint does not prevent. Then the one stored
    exactly as Google sent the address, else the oldest; never an error, since
    refusing the sign-in would lock the person out of both.
    """
    rows = session.scalars(
        select(User).where(func.lower(User.email) == address).order_by(User.created_at, User.id)
    ).all()
    if not rows:
        return None
    if len(rows) > 1:
        log.warning("auth_email_held_twice_by_case", user_ids=[row.id for row in rows])
    return next((row for row in rows if row.email == as_sent), rows[0])


def upsert_user_from_google(session: Session, identity: GoogleIdentity) -> User:
    address = normalise_email(identity.email)
    user = session.scalar(select(User).where(User.google_sub == identity.sub))

    if user is None:
        # A magic-link user may already hold this email; adopt that row.
        user = _holder(session, address, as_sent=identity.email)
        if user is not None:
            user.google_sub = identity.sub

    if user is None:
        user = User(
            id=new_id(USER),
            email=address,
            display_name=identity.name or address,
            google_sub=identity.sub,
        )
        session.add(user)
    else:
        if normalise_email(user.email) != address:
            holder = _holder(session, address, as_sent=identity.email)
            if holder is None:
                user.email = address
            else:
                log.warning("auth_google_email_taken", user_id=user.id, holder_id=holder.id)
        if identity.name:
            user.display_name = identity.name

    user.last_login_at = datetime.now(UTC)
    session.flush()
    return user
