"""add intel_action_progress and intel_action_progress_meetings

B's TeamActionProgress, kept per team for the real completion rate (#605).

Revision ID: 2a7d5c9e1f34
Revises: 8c4e1f6a2b90
Create Date: 2026-10-01 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2a7d5c9e1f34"
down_revision: str | None = "8c4e1f6a2b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intel_action_progress",
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id"),
    )
    op.create_table(
        "intel_action_progress_meetings",
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("meeting_id", sa.String(64), nullable=False),
        sa.Column("confirmed", sa.Integer(), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("overdue", sa.Integer(), nullable=False),
        sa.CheckConstraint("confirmed >= 1", name="ck_intel_action_progress_confirmed"),
        sa.CheckConstraint("done >= 0 AND done <= confirmed", name="ck_intel_action_progress_done"),
        sa.CheckConstraint(
            "overdue >= 0 AND overdue <= confirmed - done",
            name="ck_intel_action_progress_overdue",
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id", "meeting_id"),
    )
    # The meetings FK cascades on every meeting delete; without an index on the
    # referencing column that delete scans this table.
    op.create_index(
        "ix_intel_action_progress_meetings_meeting_id",
        "intel_action_progress_meetings",
        ["meeting_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_intel_action_progress_meetings_meeting_id",
        table_name="intel_action_progress_meetings",
    )
    op.drop_table("intel_action_progress_meetings")
    op.drop_table("intel_action_progress")
