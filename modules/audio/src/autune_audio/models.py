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
