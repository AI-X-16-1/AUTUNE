"""add intel_meeting_reports

Revision ID: 3d9e7b2c4f10
Revises: 8a1c5f3e9d2b
Create Date: 2026-09-29 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3d9e7b2c4f10"
down_revision: str | None = "8a1c5f3e9d2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intel_meeting_reports",
        sa.Column("meeting_id", sa.String(64), nullable=False),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("slack_channel", sa.String(64), nullable=True),
        sa.Column("slack_ts", sa.String(64), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id"),
    )
    op.create_index("ix_intel_meeting_reports_team_id", "intel_meeting_reports", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_intel_meeting_reports_team_id", table_name="intel_meeting_reports")
    op.drop_table("intel_meeting_reports")
