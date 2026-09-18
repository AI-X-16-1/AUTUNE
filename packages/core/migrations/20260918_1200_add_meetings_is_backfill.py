"""add meetings.is_backfill

**Schema proposal only.** No module writes a non-default value here yet --
opened for review by module A's owner (see the PR description) before this
column carries a real signal. See ``Meeting.is_backfill`` in entities.py for
what it's for and what still needs deciding.

``server_default`` backfills every existing row to ``false`` so the column can
be ``NOT NULL`` from day one.

Revision ID: 8e9fa0b1c2d3
Revises: 7c1f4b9e02a5
Create Date: 2026-09-18 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e9fa0b1c2d3"
down_revision: str | None = "7c1f4b9e02a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("is_backfill", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("meetings", "is_backfill")
