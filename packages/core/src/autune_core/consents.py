"""What a person agreed to: one row per document and version.

Signing in used to count as agreeing -- one sentence on the sign-in card. Now a
person opens each document and agrees to it, and that is written down here: who,
which document, which version of it, when. A document that changes gets a new
version, and nobody has agreed to that one yet, which is how a changed document
asks again.

**Two documents, and only those: the terms and the privacy policy**
(``DOCUMENTS``; decided with the user after mkkim68's review of #715,
2026-10-02). They are what using the service rests on, so the only way to
take the agreement back is to leave -- delete the account -- and a row that
can only say "agreed" is enough for them.

**This is not consent to anything else, and it permits nothing.** A consent
that a person must be able to refuse and withdraw -- to voice feature data,
to a transfer abroad -- does not belong in a table with no way to record a
withdrawal, and is refused here by name. In particular no row here lets
module A keep voice data: ``voice_profiles_enabled`` stays as it is until a
separate, refusable consent exists and A reads it (#268, #92 Q4).

**The server does not know what the documents say or which version is
current.** The text lives with the screen that shows it (the web app's legal
pages), and so does the version a person has to have agreed to. This stores
what the person's browser said they agreed to and hands it back; the screen
compares. The document has to be one of the two; of the version only the
shape is checked.

**It records; it does not gate.** No API call is refused for a missing row
(decided with the user, 2026-10-02: the screen holds a person at the consent
page, the server keeps the record). A gate in front of every module's routes is
a design conversation of its own.

Rows are the person's: deleted with their account (``ON DELETE CASCADE``), and
read only for that person -- there is no way to ask what somebody else agreed
to. Nothing is kept behind as proof once the account is gone; whether
evidence of consent should outlive the account is #92's question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .entities import UserConsent
from .errors import ValidationError

DOCUMENTS: Final = ("terms", "privacy")
"""Kept in step with the check constraint on ``user_consents.document``."""

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
    if document not in DOCUMENTS:
        raise ValidationError("not a document this record holds", field="document")
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

    That holds for two requests at once as well -- a double click, two tabs.
    Both read no row and both insert; the unique constraint lets one through,
    and the other finds the agreement already made, which is what it wanted.
    Each insert sits in its own savepoint so that losing that race undoes
    one row and not the rest of the request (review of #715).
    """
    if len(agreed) > MAX_PER_REQUEST:
        raise ValidationError("too many documents in one request", field="consents")
    wanted = list(dict.fromkeys(_checked(document, version) for document, version in agreed))
    have = {(c.document, c.version) for c in consents_of(session, user_id)}
    for document, version in wanted:
        if (document, version) in have:
            continue
        try:
            with session.begin_nested():
                session.add(UserConsent(user_id=user_id, document=document, version=version))
        except IntegrityError:
            # Recorded by another request between the read and this insert.
            continue
    return consents_of(session, user_id)
