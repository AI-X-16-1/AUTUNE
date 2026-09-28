"""add ctx_meeting_status.late_drift_notified_at

Idempotency guard for the late-lineage catch-up notify: set once
``notify_late_drift`` claims a meeting's delayed drift notice, before it is
sent. Owner: 문민재.

Revision ID: b7c6d8e9f0a1
Revises: a6b5c7d8e9f0
Create Date: 2026-09-18 10:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c6d8e9f0a1"
down_revision: str | None = "a6b5c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ctx_meeting_status",
        sa.Column("late_drift_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ctx_meeting_status", "late_drift_notified_at")
