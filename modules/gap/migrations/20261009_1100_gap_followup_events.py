"""the follow-up meeting an approver had C put on their calendar

Follow-up's proposal, once the team lead approves it, now runs C's
``schedule_followup_meeting``: an event on the approver's own calendar, the
meeting's team members who took part invited, its open gaps in the
description and a notice on the team's Slack channel. This table keeps one
row per meeting, written before Google is asked, so a second approval makes
no second event; the approver, so the event can be reached with their grant;
and the day it starts. Nothing else of the event.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: 4d8f2a6c1e57
Revises: 9c4e7a2b5d18
Create Date: 2026-10-09 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d8f2a6c1e57"
down_revision: str | None = "9c4e7a2b5d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gap_followup_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "meeting_id",
            sa.String(64),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("calendar_id", sa.String(320), nullable=True),
        sa.Column("event_id", sa.String(1024), nullable=True),
        sa.Column("event_day", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_gap_followup_events_meeting_id", "gap_followup_events", ["meeting_id"], unique=True
    )
    op.create_index("ix_gap_followup_events_user_id", "gap_followup_events", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_gap_followup_events_user_id", table_name="gap_followup_events")
    op.drop_index("ix_gap_followup_events_meeting_id", table_name="gap_followup_events")
    op.drop_table("gap_followup_events")
