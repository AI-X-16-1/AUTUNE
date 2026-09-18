"""add ctx_link_thresholds

A team's own tuned link_confidence_threshold, learned from its topic-link
confirm/reject history. Owner: 문민재.

Revision ID: c8d7e9f0a1b2
Revises: b7c6d8e9f0a1
Create Date: 2026-09-18 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d7e9f0a1b2"
down_revision: str | None = "b7c6d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ctx_link_thresholds",
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id"),
    )


def downgrade() -> None:
    op.drop_table("ctx_link_thresholds")
