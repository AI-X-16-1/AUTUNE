"""Tables owned by module A.

Every table name starts with ``aud_``. Foreign keys may reference shared
entities (``meetings.id``, ``utterances.id``) but never another module's tables.
Each table needs a path to deletion by meeting_id or user_id.

See docs/architecture/data-model.md.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from autune_core import Base


class AudConsentAttestation(Base):
    """One person's statement that everyone in a meeting's recording consented.

    **The only thing in the repository that makes ``participants.consented``
    True**, and it does so for every label in the meeting at once. Screen S10's
    per-attendee consent table cannot exist before identification (#6) gives a
    voice a person; until then the uploader's word for the whole meeting is the
    one honest statement available, and this row is where it is written down
    (#190).

    A row rather than a log line, because the row *is* the provenance module B
    asked for on #190: who said it and when, and -- once S11 lands and consent
    can also arrive per person -- the way to tell a True that came from here
    from a True that came from there. Deleting this table un-attests every
    meeting it covered; deleting an S11 answer will not touch it.

    ``meeting_id`` is the primary key: one statement per meeting, and a second
    call is the same statement. ``attested_by`` is SET NULL on account
    deletion -- consent logs are kept for audit (ui-spec section 3), the person
    is not.

    Per-person consent, revocation, and re-publishing ``TranscriptReady`` when
    consent arrives after analysis are not here. They are S10/S11 and #190's
    follow-ups; when the per-attendee table exists this one is derived from it
    or removed.
    """

    __tablename__ = "aud_consent_attestations"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    attested_by: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    attested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
