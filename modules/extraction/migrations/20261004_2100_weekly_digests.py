"""add ext_weekly_digests: which week's digest a person was sent, per team

A person is sent, on Monday in Korea, a Slack DM listing their own open action
items, through each team's bot. This is the "once" per person, team and week.
No text. Goes with the person and with the team (CASCADE).

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 6d4b2e8f1a37
Revises: 9d4e6b3f7a20
Create Date: 2026-10-04 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6d4b2e8f1a37"
down_revision: str | None = "9d4e6b3f7a20"  # extraction: add_ext_due_reminders
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_weekly_digests",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "team_id", "week_start"),
    )


def downgrade() -> None:
    op.drop_table("ext_weekly_digests")
