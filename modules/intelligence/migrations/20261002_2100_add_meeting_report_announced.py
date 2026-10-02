"""add intel_meeting_reports announced_at and correction_failed_at

What the last announcement of a person's change covered, so a change whose
announcement was lost is announced again; and when an approved correction could
not be posted at all (#698).

Revision ID: 4c8e2a1f7b90
Revises: 9d2f6b3e8a41
Create Date: 2026-10-02 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c8e2a1f7b90"
down_revision: str | None = "9d2f6b3e8a41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "intel_meeting_reports",
        sa.Column("correction_failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "intel_meeting_reports",
        sa.Column("announced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("intel_meeting_reports", "announced_at")
    op.drop_column("intel_meeting_reports", "correction_failed_at")
