"""ext_external_refs and ext_decision_refs: a Notion page per confirmed item or decision (#30)

When a person confirms an action item, module B creates one page for it in the
team's Notion database and records it here. The primary key is the item and the
system, which is what makes the page get created once however many times the
confirmation is delivered.

``ext_decision_refs`` does the same for a confirmed decision. It is keyed by
the ``dec_`` id with no foreign key to ``ext_decisions``, which a rerun
rebuilds -- the rule ``ext_decision_reviews`` follows.

Deleting the item or the meeting deletes the row; the Notion page stays, because
Autune only writes to the team's workspace.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 8d41c7e2a9f0
Revises: 6a3f9d2c81e4
Create Date: 2026-09-19 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d41c7e2a9f0"
down_revision: str | None = "6a3f9d2c81e4"  # extraction: decision_reviews_and_origin
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_external_refs",
        sa.Column(
            "action_item_id",
            sa.String(length=64),
            sa.ForeignKey("ext_action_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("system", sa.String(length=16), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.String(length=64),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("system IN ('notion','jira')", name="ck_ext_external_refs_system"),
    )
    op.create_index("ix_ext_external_refs_meeting_id", "ext_external_refs", ["meeting_id"])

    op.create_table(
        "ext_decision_refs",
        sa.Column("decision_id", sa.String(length=64), primary_key=True),
        sa.Column("system", sa.String(length=16), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.String(length=64),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("system IN ('notion','jira')", name="ck_ext_decision_refs_system"),
    )
    op.create_index("ix_ext_decision_refs_meeting_id", "ext_decision_refs", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_ext_decision_refs_meeting_id", table_name="ext_decision_refs")
    op.drop_table("ext_decision_refs")
    op.drop_index("ix_ext_external_refs_meeting_id", table_name="ext_external_refs")
    op.drop_table("ext_external_refs")
