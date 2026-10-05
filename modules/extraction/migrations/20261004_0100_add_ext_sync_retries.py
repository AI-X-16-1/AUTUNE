"""add ext_sync_retries: when "다시 시도" was last pressed for an item

Each press sends the item to Notion, the calendar and Jira again (#680). A
second press within the cooldown is refused, and this holds the time of the
last one. One time per item; it goes with the item (CASCADE).

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: b8e4f1a6c2d9
Revises: a5f7c9d2e4b1
Create Date: 2026-10-04 01:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4f1a6c2d9"
down_revision: str | None = "a5f7c9d2e4b1"  # extraction: ext_sync_failures (#754)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_sync_retries",
        sa.Column("action_item_id", sa.String(length=64), nullable=False),
        sa.Column("retried_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["action_item_id"], ["ext_action_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("action_item_id"),
    )


def downgrade() -> None:
    op.drop_table("ext_sync_retries")
