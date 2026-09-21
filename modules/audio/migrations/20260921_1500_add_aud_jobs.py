"""add aud_jobs

One row per transcription attempt. The row is what lets the upload endpoint
hand a recording to the worker without a path in the Celery payload (#275):
the worker receives a ``job_id`` and derives the file's location itself.
Owner: 김민경.

``depends_on`` pins the core revision that creates ``meetings``, so
``downgrade core@base`` drops this table first (docs/engineering/migrations.md).

Chains onto the consent revision (#283), so the audio branch has one head.

Revision ID: 5e7a1c9b2d40
Revises: 3c9d2e1f0a4b
Create Date: 2026-09-21 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e7a1c9b2d40"
down_revision: str | None = "3c9d2e1f0a4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "d34994600a9a"  # core: shared_entities


def upgrade() -> None:
    op.create_table(
        "aud_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','done','failed','superseded')",
            name="ck_aud_jobs_status",
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_aud_jobs_meeting_id", "aud_jobs", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_aud_jobs_meeting_id", table_name="aud_jobs")
    op.drop_table("aud_jobs")
