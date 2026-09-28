"""add ctx_meeting_status.late_drift_due_at

Durable "a late-lineage catch-up notify is still owed" marker -- survives a
Celery redelivery of ``on_extraction_completed`` that lands after
``extraction_seen`` already flipped, which a return-value-only signal cannot.
Owner: 문민재.

Revision ID: c9d8e0f1a2b3
Revises: b7c6d8e9f0a1
Create Date: 2026-09-18 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d8e0f1a2b3"
down_revision: str | None = "b7c6d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ctx_meeting_status",
        sa.Column("late_drift_due_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ctx_meeting_status", "late_drift_due_at")
