"""add aud_masking_rules

A team's own masking rules, learned from S30 reports (#555). A row is a shape
-- character classes and separators, `A-#####` -- never the reported text.
Reachable for deletion through `team_id` (CASCADE); `created_by` is
provenance and clears when that person's account goes.

Owner: 김민경.

Revision ID: d2e8b04f6a17
Revises: c7a1d93e5f20
Create Date: 2026-10-01 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2e8b04f6a17"
down_revision: str | None = "c7a1d93e5f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aud_masking_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("shape", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "shape", name="uq_aud_masking_rules_team_shape"),
    )
    op.create_index("ix_aud_masking_rules_team_id", "aud_masking_rules", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_aud_masking_rules_team_id", table_name="aud_masking_rules")
    op.drop_table("aud_masking_rules")
