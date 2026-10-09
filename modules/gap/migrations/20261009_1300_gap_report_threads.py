"""where E posted a meeting's report, for C's cards to reply in its thread

S20's question cards and "담당자 지정해 질문" (#824) reply in the thread of
the meeting's report once E has posted it. E says where with
``autune.intelligence.meeting_report_posted``; this keeps the channel id and
the message ``ts`` -- where the message is, nothing it says. One row per
meeting, gone with the meeting.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: 4d1f8a6c2b37
Revises: 4d8f2a6c1e57
Create Date: 2026-10-09 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d1f8a6c2b37"
down_revision: str | None = "4d8f2a6c1e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gap_report_threads",
        sa.Column(
            "meeting_id",
            sa.String(length=64),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("thread_ts", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("gap_report_threads")
