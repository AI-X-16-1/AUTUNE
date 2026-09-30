"""ext_action_item_related: the other lines an item's summary was written from

The LLM resolver summarises a commitment from the lines around it and from lines
elsewhere in the meeting that are about the same thing, and says which it used.
Those are kept here so the drawer can show them beneath the summary for a person
to check and correct. Not more rows in ``ext_action_item_sources``: those are what
an item is, and D and E read a count of them.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 6d2e9a4b7c15
Revises: 4d9a2c7e1f35
Create Date: 2026-09-30 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6d2e9a4b7c15"
down_revision: str | None = "4d9a2c7e1f35"  # extraction: notion_targets
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_action_item_related",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("action_item_id", sa.String(length=64), nullable=False),
        sa.Column("utterance_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["action_item_id"], ["ext_action_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["utterance_id"],
            ["utterances.id"],
            name="fk_ext_action_item_related_utt",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_item_id", "utterance_id", name="uq_ext_action_item_related"),
    )
    op.create_index(
        op.f("ix_ext_action_item_related_action_item_id"),
        "ext_action_item_related",
        ["action_item_id"],
    )
    op.create_index(
        op.f("ix_ext_action_item_related_utterance_id"),
        "ext_action_item_related",
        ["utterance_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_ext_action_item_related_utterance_id"), table_name="ext_action_item_related"
    )
    op.drop_index(
        op.f("ix_ext_action_item_related_action_item_id"), table_name="ext_action_item_related"
    )
    op.drop_table("ext_action_item_related")
