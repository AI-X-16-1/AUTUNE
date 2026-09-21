"""ext_decision_reviews, and ext_decisions.origin: people review and add decisions (#246)

Nothing about a decision leaves Autune until somebody confirms it. The new table
holds that answer: pending, confirmed or rejected, and an optional rewording.

``ext_decisions.origin`` tells a decision the pipeline proposed (``model``, every
existing row) from one a person added (``user``). A rerun rebuilds only the
model's. Downgrade deletes the ``user`` rows: before this revision nothing could
tell them apart, and the next rerun would have deleted them anyway.

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

    op.add_column(
        "ext_decisions",
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="model"),
    )
    op.create_check_constraint(
        "ck_ext_decisions_origin", "ext_decisions", "origin IN ('model','user')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_ext_decisions_origin", "ext_decisions", type_="check")
    op.execute("DELETE FROM ext_decisions WHERE origin = 'user'")
    op.drop_column("ext_decisions", "origin")
    op.drop_index("ix_ext_decision_reviews_meeting_id", table_name="ext_decision_reviews")
    op.drop_table("ext_decision_reviews")
