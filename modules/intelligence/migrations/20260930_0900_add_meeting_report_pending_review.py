"""add intel_meeting_reports.pending_review

Revision ID: 5b2f8a1d7c33
Revises: 3d9e7b2c4f10
Create Date: 2026-09-30 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b2f8a1d7c33"
down_revision: str | None = "3d9e7b2c4f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "intel_meeting_reports",
        sa.Column("pending_review", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("intel_meeting_reports", "pending_review")
