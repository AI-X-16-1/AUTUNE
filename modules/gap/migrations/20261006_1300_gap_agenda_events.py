"""gap_agenda_events and gap_agenda_cleanup; gap_questions goes

S20's "담당자 지정해 질문" no longer writes to a teammate's calendar (mkkim68 on
#824: one person's grant is for their own work only), so ``gap_questions`` has
nothing to hold. It is dropped here rather than its revision removed, because
the dev database already carries that revision.

"다음 회의 잡기" now records which event on whose calendar holds each gap's
line (``gap_agenda_events``, with the meeting and the owner), so the line can
be taken out when the gap is taken back, the meeting goes or the account
goes; ``gap_agenda_cleanup`` holds the lines of a deleted meeting until the
worker has taken them out.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: 7d2e9b4f1c60
Revises: 3c8f1a6e9d27
Create Date: 2026-10-06 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d2e9b4f1c60"
down_revision: str | None = "3c8f1a6e9d27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_gap_questions_user_id", table_name="gap_questions")
    op.drop_table("gap_questions")

    op.create_table(
        "gap_agenda_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "meeting_id",
            sa.String(64),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gap_id", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("calendar_id", sa.String(320), nullable=False),
        sa.Column("event_id", sa.String(1024), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("gap_id", "user_id", "event_id", name="uq_gap_agenda_events"),
    )
    op.create_index("ix_gap_agenda_events_meeting_id", "gap_agenda_events", ["meeting_id"])
    op.create_index("ix_gap_agenda_events_user_id", "gap_agenda_events", ["user_id"])

    op.create_table(
        "gap_agenda_cleanup",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("calendar_id", sa.String(320), nullable=False),
        sa.Column("event_id", sa.String(1024), nullable=False),
        sa.Column("gap_id", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "event_id", "gap_id", name="uq_gap_agenda_cleanup"),
    )
    op.create_index("ix_gap_agenda_cleanup_user_id", "gap_agenda_cleanup", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_gap_agenda_cleanup_user_id", table_name="gap_agenda_cleanup")
    op.drop_table("gap_agenda_cleanup")
    op.drop_index("ix_gap_agenda_events_user_id", table_name="gap_agenda_events")
    op.drop_index("ix_gap_agenda_events_meeting_id", table_name="gap_agenda_events")
    op.drop_table("gap_agenda_events")

    op.create_table(
        "gap_questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "gap_id",
            sa.String(64),
            sa.ForeignKey("gap_gaps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("calendar_id", sa.String(320), nullable=False),
        sa.Column("event_id", sa.String(1024), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("gap_id", "user_id", name="uq_gap_questions_gap_user"),
    )
    op.create_index("ix_gap_questions_user_id", "gap_questions", ["user_id"])
