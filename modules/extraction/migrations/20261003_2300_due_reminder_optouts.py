"""add ext_due_reminder_optouts: a person who turned the due-date reminders off

The reminders (#751) go to an item's assignee by Slack DM. A person can turn
them off for themselves; this table holds who did. On unless a row says off.

It holds the person and when they turned it off, nothing else. It goes with
the account (CASCADE).

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 6b2e9d4a7c13
Revises: 9d4e6b3f7a20
Create Date: 2026-10-03 23:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6b2e9d4a7c13"
down_revision: str | None = "9d4e6b3f7a20"  # extraction: add_ext_due_reminders (#751)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_due_reminder_optouts",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("ext_due_reminder_optouts")
