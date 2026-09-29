"""ext_calendar_events and ext_calendar_polls: each person's own due dates on
their own calendar, and when B last read it back (#435)

A confirmed action item with a due date becomes one all-day event on its
assignee's Google Calendar, through that person's own grant in
``user_integrations`` (core). ``ext_calendar_events`` records which event and
whose calendar, one row per item, plus the date last synced -- what the
read-back compares against. ``ext_calendar_polls`` is when B last read a
person's calendar back, B's own sync state.

Both go with their user (``ON DELETE CASCADE``); the events go with their item
and meeting as well.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 7c2e5a9d4b18
Revises: e92331919850
Create Date: 2026-09-29 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c2e5a9d4b18"
down_revision: str | None = "e92331919850"  # extraction: action_item_description_resolved
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_calendar_events",
        sa.Column(
            "action_item_id",
            sa.String(length=64),
            sa.ForeignKey("ext_action_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "meeting_id",
            sa.String(length=64),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=1024), nullable=True),
        sa.Column("synced_due_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_ext_calendar_events_meeting_id", "ext_calendar_events", ["meeting_id"])
    op.create_index("ix_ext_calendar_events_user_id", "ext_calendar_events", ["user_id"])
    op.create_table(
        "ext_calendar_polls",
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("polled_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ext_calendar_polls")
    op.drop_index("ix_ext_calendar_events_user_id", table_name="ext_calendar_events")
    op.drop_index("ix_ext_calendar_events_meeting_id", table_name="ext_calendar_events")
    op.drop_table("ext_calendar_events")
