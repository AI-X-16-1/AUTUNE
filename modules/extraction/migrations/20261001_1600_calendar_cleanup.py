"""ext_calendar_cleanup: due-date events still to take off a person's calendar

A meeting that expires (#581) takes its items, and with them
``ext_calendar_events``, the only record of which event on which person's
calendar each due date became. The meeting hook copies ``(user_id, event_id)``
here before the meeting goes, and a periodic task removes the events (#588).
No meeting foreign key, so the row outlives the meeting; ``user_id`` cascades,
because the event can only be removed with that person's own grant, and an
account deletion removes its events in its own hook.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 6a1d3f8b2e47
Revises: 9a3c5e7b1d24
Create Date: 2026-10-01 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6a1d3f8b2e47"
down_revision: str | None = "9a3c5e7b1d24"  # extraction: jira_pulled_at
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_calendar_cleanup",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=1024), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_ext_calendar_cleanup_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "event_id", name="uq_ext_calendar_cleanup"),
    )
    op.create_index("ix_ext_calendar_cleanup_user_id", "ext_calendar_cleanup", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ext_calendar_cleanup_user_id", "ext_calendar_cleanup")
    op.drop_table("ext_calendar_cleanup")
