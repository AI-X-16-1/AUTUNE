"""add ext_minutes_events: a project's minutes on the sender's own calendar

An all-day event on the meeting's day, in the calendar of the person who
pressed send. The event id only, kept so sending again updates it and so it is
removed when the meeting expires, the account is deleted or the project goes.
Goes with the meeting, the project and the user (CASCADE).

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 2a9d5f7c3e18
Revises: 8e1b6d3a9f45
Create Date: 2026-10-04 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2a9d5f7c3e18"
down_revision: str | None = "8e1b6d3a9f45"  # extraction: project_sends
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_minutes_events",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["ext_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "project_id", "user_id"),
    )
    op.create_index("ix_ext_minutes_events_user_id", "ext_minutes_events", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ext_minutes_events_user_id", table_name="ext_minutes_events")
    op.drop_table("ext_minutes_events")
