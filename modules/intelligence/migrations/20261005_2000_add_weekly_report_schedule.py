"""add intel_team_settings and intel_reports posted_at / not_posted

When a team's weekly report is sent and whether an empty week is posted, and
whether a report went out, so the hourly task posts each one once (#227).

Revision ID: 5b9e3d7a2c14
Revises: 4c8e2a1f7b90
Create Date: 2026-10-05 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b9e3d7a2c14"
down_revision: str | None = "4c8e2a1f7b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intel_team_settings",
        sa.Column(
            "team_id",
            sa.String(64),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("weekly_report_weekday", sa.Integer(), nullable=False),
        sa.Column("weekly_report_hour", sa.Integer(), nullable=False),
        sa.Column(
            "weekly_report_send_empty", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("updated_by", sa.String(64), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "weekly_report_weekday BETWEEN 0 AND 6", name="ck_intel_team_settings_weekday"
        ),
        sa.CheckConstraint(
            "weekly_report_hour BETWEEN 0 AND 23", name="ck_intel_team_settings_hour"
        ),
    )
    op.add_column("intel_reports", sa.Column("posted_at", sa.DateTime(timezone=True)))
    op.add_column("intel_reports", sa.Column("not_posted", sa.String(16)))


def downgrade() -> None:
    op.drop_column("intel_reports", "not_posted")
    op.drop_column("intel_reports", "posted_at")
    op.drop_table("intel_team_settings")
