"""stage upstream payloads

Revision ID: 40c92c025eb8
Revises: 6645e7449232
Create Date: 2026-09-09 17:21:55.507146
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "40c92c025eb8"
down_revision: str | None = "6645e7449232"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "intel_completion", sa.Column("extraction_payload", postgresql.JSONB(), nullable=True)
    )
    op.add_column("intel_completion", sa.Column("gap_payload", postgresql.JSONB(), nullable=True))
    op.add_column(
        "intel_completion", sa.Column("context_payload", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("intel_completion", "context_payload")
    op.drop_column("intel_completion", "gap_payload")
    op.drop_column("intel_completion", "extraction_payload")
