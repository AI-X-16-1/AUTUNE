"""ext_decision_reviews: a person's verdict on each proposed decision (#246)

Nothing about a decision leaves Autune until somebody confirms it. This table
holds that answer: pending, confirmed or rejected, and an optional rewording.

Keyed by the ``dec_`` id with no foreign key to ``ext_decisions``, because a
rerun deletes and rebuilds those rows and would take every review with it. The
meeting foreign key cascades, so a deleted meeting takes its reviews.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 6a3f9d2c81e4
Revises: 3df3e0a67a67
Create Date: 2026-09-17 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6a3f9d2c81e4"
down_revision: str | None = "3df3e0a67a67"  # extraction: confirmations_before_the_dm
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_decision_reviews",
        sa.Column("decision_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.String(length=64),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending','confirmed','rejected')",
            name="ck_ext_decision_reviews_status",
        ),
    )
    op.create_index("ix_ext_decision_reviews_meeting_id", "ext_decision_reviews", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_ext_decision_reviews_meeting_id", table_name="ext_decision_reviews")
    op.drop_table("ext_decision_reviews")
