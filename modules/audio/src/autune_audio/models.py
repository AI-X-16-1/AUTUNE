"""Tables owned by module A.

Every table name starts with ``aud_``. Foreign keys may reference shared
entities (``meetings.id``, ``utterances.id``) but never another module's tables.
Each table needs a path to deletion by meeting_id or user_id.

See docs/architecture/data-model.md.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from autune_core import Base, Meeting
from autune_core.ids import JOB, new_id

JOB_STATUSES = ("queued", "running", "done", "failed", "superseded")


class TranscriptionJob(Base):
    """One attempt to transcribe one meeting: ``aud_jobs``.

    The row exists so that the recording can be handed from the upload
    endpoint to the worker **without a path in the Celery payload**, which
    privacy.md section 1 forbids by name. The worker receives only ``job_id``
    and derives the file's location itself (``storage.upload_path``); the
    message on the broker, and Celery's failure output, carry an opaque id.

    An attempt, not a meeting. A ``failed`` meeting accepts another recording,
    and if both attempts shared one filename a late first task could adopt
    the second attempt's file and then fail the meeting the second attempt
    was busy with (@lsh2217 on #275). Each attempt owns its own file, and a
    ``superseded`` job is one the worker declines to run.

    Lifecycle: ``queued`` at the claim; ``running`` when the worker picks it
    up; ``done`` or ``failed`` when it stops; ``superseded`` when a later
    attempt for the same meeting was accepted first. ``finished_at`` is set on
    the last three, and the orphan sweep reads it (``service.sweep_orphans``).

    Cascades from ``meetings`` -- the deletion path privacy.md section 4
    requires. Nothing here is meeting content: ids, statuses, timestamps.
    """

    __tablename__ = "aud_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','done','failed','superseded')",
            name="ck_aud_jobs_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(JOB))
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    meeting: Mapped[Meeting] = relationship()


class AudConsentAttestation(Base):
    """One person's statement that everyone in a meeting's recording consented.

    **The only thing in the repository that makes ``participants.consented``
    True**, and it does so for every label in the meeting at once. Screen S10's
    per-attendee consent table cannot exist before identification (#6) gives a
    voice a person; until then a team member's word for the whole meeting is
    the one honest statement available, and this row is where it is written
    down (#190). Any member of the meeting's team may make it -- there is no
    ``created_by`` on a meeting to narrow it to the person who uploaded, and a
    member who was not in the room can attest for those who were. That is a
    limit of the statement, stated here and in ``docs/modules/audio.md``.

    A row rather than a log line, because the row *is* the provenance module B
    asked for on #190: who said it and when, and -- once S11 lands and consent
    can also arrive per person -- the way to tell a True that came from here
    from a True that came from there.

    **Deleting the row is not a revocation.** Nothing sets a participant back
    to False, so a deleted row leaves the rows already set True as they are and
    only a later rerun's new labels would come out False -- a meeting whose
    labels disagree about consent, which #190 forbids. There is no route that
    deletes it and it is not to be deleted by hand. Revocation, when S10/S11
    define it, resets the participants as well as removing this.

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
