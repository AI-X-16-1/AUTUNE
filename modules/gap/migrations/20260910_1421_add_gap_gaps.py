"""add gap_gaps and gap_related_topics

The findings themselves, and which topics each was inferred from.

Separate from the topic-graph revision because the two are separable: the
graph is worth building and reading before anything scores a gap off it, and a
downgrade can stop here and still leave a usable graph.

``dismissed_at`` is a timestamp, not a soft-delete flag — the row stays visible
and marked, and threshold tuning reads the mark (ADR 0006). No responder id is
stored with it; see the model docstring.

Chains onto the topic-graph revision and inherits its ``depends_on`` on core.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: b8d4fa2c5e31
Revises: a7c3e91b4d20
Create Date: 2026-09-10 14:21:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d4fa2c5e31"
down_revision: str | None = "a7c3e91b4d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gap_gaps",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=400), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("template_item", sa.String(length=400), nullable=True),
        sa.Column("suggested_question", sa.Text(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("severity IN ('high', 'medium', 'low')", name="ck_gap_gaps_severity"),
        sa.CheckConstraint("risk_score >= 0 AND risk_score <= 1", name="ck_gap_gaps_risk_score"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gap_gaps_meeting_id", "gap_gaps", ["meeting_id"])

    op.create_table(
        "gap_related_topics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("gap_id", sa.String(length=64), nullable=False),
        sa.Column("topic_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["gap_id"], ["gap_gaps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["gap_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gap_id", "topic_id", name="uq_gap_related_topics"),
    )
    op.create_index("ix_gap_related_topics_gap_id", "gap_related_topics", ["gap_id"])
    op.create_index("ix_gap_related_topics_topic_id", "gap_related_topics", ["topic_id"])


def downgrade() -> None:
    op.drop_table("gap_related_topics")
    op.drop_table("gap_gaps")
