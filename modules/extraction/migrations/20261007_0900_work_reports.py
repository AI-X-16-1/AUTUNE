"""add ext_work_reports: that a person was sent the work-report draft for one day

A person who finished or started something today is sent, that afternoon, a
draft report of their own items on one team (``work_report``, the user
2026-10-07). This table is the "once": one row per person, team and day,
written before the message goes. No text -- the message is not kept. A row is
deleted once its day has passed (the sending task, every run): the draft goes
only on a day the person finished or started something, so a row kept would
say which days they worked. Goes with the person and with the team before
that.

Its own table, not ``ext_daily_digests``: that one's key is the same three
columns, and its latest row is where the next morning DM counts from.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 8e5c1a7d3f94
Revises: 4d7b1e9a2c58
Create Date: 2026-10-07 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e5c1a7d3f94"
down_revision: str | None = "4d7b1e9a2c58"  # extraction: decision statement resolved
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_work_reports",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "team_id", "day"),
    )


def downgrade() -> None:
    op.drop_table("ext_work_reports")
