"""add ext_sync_failures: that an item's latest copy to an outside system failed, its kind and time

A failed send rolls its claim in ``ext_external_refs`` back, so nothing was
left for the board to draw: it could say "sent" or "sending", never "failed"
(#680). This keeps the latest failure per item and system -- one of four kinds
and a time, never the outside service's message and never what was being sent.
The next attempt that succeeds deletes the row; it goes with the item.

Chained onto the due-reminders revision so module B keeps one head.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: a5f7c9d2e4b1
Revises: 9d4e6b3f7a20
Create Date: 2026-10-02 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5f7c9d2e4b1"
down_revision: str | None = "9d4e6b3f7a20"  # extraction: ext_due_reminders
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_sync_failures",
        sa.Column("action_item_id", sa.String(length=64), nullable=False),
        sa.Column("system", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "system IN ('notion','jira','calendar')", name="ck_ext_sync_failures_system"
        ),
        sa.CheckConstraint(
            "kind IN ('privacy','reconnect','unreachable','rejected')",
            name="ck_ext_sync_failures_kind",
        ),
        sa.ForeignKeyConstraint(["action_item_id"], ["ext_action_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("action_item_id", "system"),
    )


def downgrade() -> None:
    op.drop_table("ext_sync_failures")
