"""What a person agreed to: one row per document and version.

Signing in used to count as agreeing -- one sentence on the sign-in card. Now a
person opens each document and agrees to it, and that is written down here: who,
which document, which version of it, when. A document that changes gets a new
version, and nobody has agreed to that one yet, which is how a changed document
asks again.

**The server does not know what the documents say or which version is
current.** The text lives with the screen that shows it
(``apps/web/src/app/legal``), and so does the list of what has to be agreed to.
This stores what the person's browser said they agreed to and hands it back;
the screen compares. Only the shape of a name and a version is checked here.

**It records; it does not gate.** No API call is refused for a missing row
(decided with the user, 2026-10-02: the screen holds a person at the consent
page, the server keeps the record). A gate in front of every module's routes is
a design conversation of its own.

Rows are the person's: deleted with their account (``ON DELETE CASCADE``), and
read only for that person -- there is no way to ask what somebody else agreed
to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from .entities import UserConsent
from .errors import ValidationError

_DOCUMENT: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_VERSION: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")

MAX_PER_REQUEST: Final = 20
"""More documents than any consent page will hold; a bound on what one call
can write."""


@dataclass(frozen=True)
class Consent:
    document: str
    version: str
    agreed_at: datetime | None = None


def _checked(document: str, version: str) -> tuple[str, str]:
    if not _DOCUMENT.fullmatch(document):
        raise ValidationError("not a document name", field="document")
    if not _VERSION.fullmatch(version):
        raise ValidationError("not a document version", field="version")
    return document, version


def consents_of(session: Session, user_id: str) -> list[Consent]:
    """Everything this person agreed to, oldest first."""
    rows = session.scalars(
        select(UserConsent)
        .where(UserConsent.user_id == user_id)
        .order_by(UserConsent.agreed_at, UserConsent.id)
    )
    return [Consent(row.document, row.version, row.agreed_at) for row in rows]


def record_consents(session: Session, user_id: str, agreed: list[tuple[str, str]]) -> list[Consent]:
    """Write down that this person agreed to each ``(document, version)``.

    Agreeing twice is one agreement: the first row and its time are kept, so
    the record says when a person first agreed to that version, not when they
    last reloaded the page. Returns everything they have agreed to.
    """
    if len(agreed) > MAX_PER_REQUEST:
        raise ValidationError("too many documents in one request", field="consents")
    wanted = list(dict.fromkeys(_checked(document, version) for document, version in agreed))
    have = {(c.document, c.version) for c in consents_of(session, user_id)}
    for document, version in wanted:
        if (document, version) not in have:
            session.add(UserConsent(user_id=user_id, document=document, version=version))
    session.flush()
    return consents_of(session, user_id)
