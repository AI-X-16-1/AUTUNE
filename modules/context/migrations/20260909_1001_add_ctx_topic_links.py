"""add ctx_topic_links

Meeting-to-meeting topic links with retrieval and re-rank scores.
``linked_meeting_id`` is ``ON DELETE SET NULL``: a retention sweep may remove the
past meeting, and the UI then shows it is gone rather than reconstructing it.
Owner: 문민재.

Revision ID: c2d1e3f4a5b6
Revises: b1c0d2e3f4a5
Create Date: 2026-09-09 10:01:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d1e3f4a5b6"
down_revision: str | None = "b1c0d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ctx_topic_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("topic_label", sa.String(length=400), nullable=False),
        sa.Column("linked_meeting_id", sa.String(length=64), nullable=True),
        sa.Column("linked_meeting_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("similarity", sa.Float(), nullable=False),
        sa.Column("rerank_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("retriever_version", sa.String(length=200), nullable=False),
        sa.Column("reranker_version", sa.String(length=200), nullable=False),
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
            "status IN ('asserted', 'pending', 'confirmed', 'rejected')",
            name="ck_ctx_topic_links_status",
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["linked_meeting_id"], ["meetings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ctx_topic_links_meeting_id", "ctx_topic_links", ["meeting_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ctx_topic_links_meeting_id", table_name="ctx_topic_links")
    op.drop_table("ctx_topic_links")
