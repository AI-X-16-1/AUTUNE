"""add ext_action_items, ext_action_item_sources and ext_edit_events

The first real revision on the extraction branch. Chains onto the branch anchor
and touches only ext_* tables.

Depends on the core shared_entities revision: every table here reaches deletion
through meetings.id, and two reference users.id and utterances.id.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 4c1e8b90d3a7
Revises: 88a66e7bcaf4
Create Date: 2026-09-09 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c1e8b90d3a7"
down_revision: str | None = "88a66e7bcaf4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "d34994600a9a"  # core: shared_entities


def upgrade() -> None:
    op.create_table(
        "ext_action_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("assignee_id", sa.String(length=64), nullable=True),
        sa.Column("assignee_label", sa.String(length=200), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('needs_confirmation','todo','in_progress','done')",
            name="ck_ext_action_items_status",
        ),
        sa.CheckConstraint("origin IN ('model','user')", name="ck_ext_action_items_origin"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_ext_action_items_confidence"
        ),
    )
    op.create_index("ix_ext_action_items_meeting_id", "ext_action_items", ["meeting_id"])
    op.create_index("ix_ext_action_items_assignee_id", "ext_action_items", ["assignee_id"])

    op.create_table(
        "ext_action_item_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("action_item_id", sa.String(length=64), nullable=False),
        sa.Column("utterance_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["action_item_id"], ["ext_action_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["utterance_id"], ["utterances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_item_id", "utterance_id", name="uq_ext_action_item_sources"),
    )
    op.create_index(
        "ix_ext_action_item_sources_action_item_id", "ext_action_item_sources", ["action_item_id"]
    )
    op.create_index(
        "ix_ext_action_item_sources_utterance_id", "ext_action_item_sources", ["utterance_id"]
    )

    op.create_table(
        "ext_edit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("action_item_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_item_id"], ["ext_action_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('created','deleted','edited')", name="ck_ext_edit_events_kind"
        ),
    )
    op.create_index("ix_ext_edit_events_meeting_id", "ext_edit_events", ["meeting_id"])
    op.create_index("ix_ext_edit_events_action_item_id", "ext_edit_events", ["action_item_id"])


def downgrade() -> None:
    op.drop_table("ext_edit_events")
    op.drop_table("ext_action_item_sources")
    op.drop_table("ext_action_items")
