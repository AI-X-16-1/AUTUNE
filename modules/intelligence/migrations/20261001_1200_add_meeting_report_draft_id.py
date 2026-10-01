"""add intel_meeting_reports.draft_id

Revision ID: 8c4e1f6a2b90
Revises: 5b2f8a1d7c33
Create Date: 2026-10-01 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c4e1f6a2b90"
down_revision: str | None = "5b2f8a1d7c33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: a draft stored before this column, and a post approved before
    # it, carry no id and post as they did.
    op.add_column("intel_meeting_reports", sa.Column("draft_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("intel_meeting_reports", "draft_id")
