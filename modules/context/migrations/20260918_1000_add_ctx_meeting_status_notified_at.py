"""add ctx_meeting_status.notified_at

Idempotency guard for the Slack notify step: set once
``notify_context_events`` claims a meeting's notices, before any are sent.
Owner: 문민재.

Revision ID: a6b5c7d8e9f0
Revises: f5a4b6c7d8e9
Create Date: 2026-09-18 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6b5c7d8e9f0"
down_revision: str | None = "f5a4b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ctx_meeting_status",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ctx_meeting_status", "notified_at")
