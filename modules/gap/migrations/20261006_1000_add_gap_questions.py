"""gap_questions: a gap's question put on one teammate's calendar

S20's "담당자 지정해 질문" had nowhere to write (#824). One row per gap and
person asked, so a second press for the same person makes no second event and
S20 can say who was already asked. Who pressed the button is not stored.

Goes with its gap (and so its meeting) and with the person asked.

Chains onto the carried-at revision, the gap branch's head on ``main``.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: 3c8f1a6e9d27
Revises: 9b2e4c7a1d53
Create Date: 2026-10-06 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3c8f1a6e9d27"
down_revision: str | None = "9b2e4c7a1d53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_gap_questions_user_id", table_name="gap_questions")
    op.drop_table("gap_questions")
