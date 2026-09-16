"""add intel_gap_patterns.classifier_version

Revision ID: 6b7ae8dc6f4a
Revises: 40c92c025eb8
Create Date: 2026-09-14 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6b7ae8dc6f4a"
down_revision: str | None = "40c92c025eb8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "intel_gap_patterns",
        sa.Column("classifier_version", sa.String(length=200), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("intel_gap_patterns", "classifier_version")
