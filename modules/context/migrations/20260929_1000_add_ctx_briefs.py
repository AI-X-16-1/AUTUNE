"""add ctx_briefs

One row per scheduled meeting whose pre-meeting brief has been composed: which
past meeting it recaps and when it was sent. Stores the choice, not the recap
-- see ``autune_context.models.CtxBrief``. Owner: 문민재.

Revision ID: 7b5d2880cba1
Revises: 7696c4b2cfb5
Create Date: 2026-09-29 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b5d2880cba1"
down_revision: str | None = "7696c4b2cfb5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ctx_briefs",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("previous_meeting_id", sa.String(length=64), nullable=True),
        sa.Column("match_reason", sa.String(length=16), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "match_reason IS NULL OR match_reason IN ('series', 'topic', 'latest')",
            name="ck_ctx_briefs_match_reason",
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["previous_meeting_id"], ["meetings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("meeting_id"),
    )


def downgrade() -> None:
    op.drop_table("ctx_briefs")
