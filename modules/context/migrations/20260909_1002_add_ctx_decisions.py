"""add ctx_decisions

Decision threads: the lineage identity (``thr_``) that persists across meetings.
Anchored on ``team_id`` so a lineage survives its origin meeting reaching the
retention window. A thread left with no versions is swept by the meeting-deletion
hook in autune_context.service. Owner: 문민재.

Revision ID: d3e2f4a5b6c7
Revises: c2d1e3f4a5b6
Create Date: 2026-09-09 10:02:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3e2f4a5b6c7"
down_revision: str | None = "c2d1e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ctx_decisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("topic_label", sa.String(length=400), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ctx_decisions_team_id", "ctx_decisions", ["team_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ctx_decisions_team_id", table_name="ctx_decisions")
    op.drop_table("ctx_decisions")
