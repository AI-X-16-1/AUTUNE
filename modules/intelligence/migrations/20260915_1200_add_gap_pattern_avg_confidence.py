"""add intel_gap_patterns.avg_confidence

Revision ID: 8a1c5f3e9d2b
Revises: 6b7ae8dc6f4a
Create Date: 2026-09-15 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8a1c5f3e9d2b"
down_revision: str | None = "6b7ae8dc6f4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "intel_gap_patterns",
        sa.Column("avg_confidence", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("intel_gap_patterns", "avg_confidence")
