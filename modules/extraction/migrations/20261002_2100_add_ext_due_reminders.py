"""add ext_due_reminders: which due-date reminder an item's assignee was already sent

A reminder goes to the assignee once the day before the due date and once after
it passes (``autune_extraction.reminders``). This is the "once": the item, the
kind and the due date it was about. A date moved is a new date, so a new row.

It holds no text and names no person -- only that a message of that kind went
for that item. It goes with the item (CASCADE), and so with its meeting.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 9d4e6b3f7a20
Revises: 8c3f5a2d6e19
Create Date: 2026-10-02 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d4e6b3f7a20"
down_revision: str | None = "8c3f5a2d6e19"  # extraction: confirmation_dm_place
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_due_reminders",
        sa.Column("action_item_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('due_soon','overdue')", name="ck_ext_due_reminders_kind"),
        sa.ForeignKeyConstraint(["action_item_id"], ["ext_action_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("action_item_id", "kind", "due_date"),
    )


def downgrade() -> None:
    op.drop_table("ext_due_reminders")
