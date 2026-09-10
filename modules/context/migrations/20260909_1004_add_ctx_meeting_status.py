"""add ctx_meeting_status

Completion tracking for D's two halves plus the B-timeout deadline. One row per
meeting; ``published_at`` makes ``publish_if_ready`` idempotent. Owner: 문민재.

Revision ID: f5a4b6c7d8e9
Revises: e4f3a5b6c7d8
Create Date: 2026-09-09 10:04:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5a4b6c7d8e9"
down_revision: str | None = "e4f3a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ctx_meeting_status",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("topic_linking_done", sa.Boolean(), nullable=False),
        sa.Column("lineage_done", sa.Boolean(), nullable=False),
        sa.Column("extraction_seen", sa.Boolean(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id"),
    )


def downgrade() -> None:
    op.drop_table("ctx_meeting_status")
