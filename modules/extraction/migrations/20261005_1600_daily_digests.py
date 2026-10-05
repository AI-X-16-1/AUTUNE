"""add ext_daily_digests and ext_notification_pauses: the morning DM and a person's own leave dates

ext_daily_digests: that a person was sent the morning DM for one day through
one team's Slack. The primary key is the "once"; the latest row's time is
where the next DM's "since the last one" starts. No text. Goes with the person
and with the team.

ext_notification_pauses: one range of days a person asked not to get the
morning DM or Monday's digest. Dates only, the person's own, deleted once the
range has ended. Goes with the account.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 1b7d4e9a6c52
Revises: 7c3e9a1d5b42
Create Date: 2026-10-05 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1b7d4e9a6c52"
down_revision: str | None = "7c3e9a1d5b42"  # extraction: decision jira site (#796)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_daily_digests",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "team_id", "day"),
    )
    op.create_table(
        "ext_notification_pauses",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_ext_notification_pauses_order"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("ext_notification_pauses")
    op.drop_table("ext_daily_digests")
