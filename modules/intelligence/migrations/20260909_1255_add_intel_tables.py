"""add intel tables

Revision ID: 6645e7449232
Revises: 4e64e50ad2cf
Create Date: 2026-09-09 12:55:34.835541
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6645e7449232"
down_revision: str | None = "4e64e50ad2cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "d34994600a9a"


def upgrade() -> None:
    op.create_table(
        "intel_completion",
        sa.Column("meeting_id", sa.String(64), nullable=False),
        sa.Column("extraction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gap_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("context_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("aggregated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id"),
    )
    op.create_table(
        "intel_scores",
        sa.Column("meeting_id", sa.String(64), nullable=False),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("grade", sa.String(1), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("decision_density", sa.Float(), nullable=True),
        sa.Column("gap_count", sa.Integer(), nullable=True),
        sa.Column("action_item_completion_rate", sa.Float(), nullable=True),
        sa.Column("participation_balance", sa.Float(), nullable=True),
        sa.Column("missing_sources", postgresql.JSONB(), server_default="[]", nullable=False),
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
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id"),
        sa.CheckConstraint("grade IN ('A','B','C','D','E','F')", name="ck_intel_scores_grade"),
        sa.CheckConstraint("value >= 0 AND value <= 1", name="ck_intel_scores_value_unit"),
    )
    op.create_index("ix_intel_scores_team_id", "intel_scores", ["team_id"])
    op.create_table(
        "intel_gap_patterns",
        sa.Column("meeting_id", sa.String(64), nullable=False),
        sa.Column("pattern_type", sa.String(100), nullable=False),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("source_gap_ids", postgresql.JSONB(), server_default="[]", nullable=False),
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
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "pattern_type"),
    )
    op.create_index("ix_intel_gap_patterns_team_id", "intel_gap_patterns", ["team_id"])
    op.create_table(
        "intel_alignment",
        sa.Column("meeting_id", sa.String(64), nullable=False),
        sa.Column("role_a", sa.String(50), nullable=False),
        sa.Column("role_b", sa.String(50), nullable=False),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
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
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "role_a", "role_b"),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="ck_intel_alignment_score_unit"),
    )
    op.create_index("ix_intel_alignment_team_id", "intel_alignment", ["team_id"])
    op.create_table(
        "intel_predictions",
        sa.Column("meeting_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=True),
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
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "kind", "horizon_days"),
        sa.CheckConstraint(
            "probability >= 0 AND probability <= 1", name="ck_intel_predictions_probability_unit"
        ),
        sa.CheckConstraint("horizon_days > 0", name="ck_intel_predictions_horizon_positive"),
    )
    op.create_index("ix_intel_predictions_team_id", "intel_predictions", ["team_id"])
    op.create_table(
        "intel_reports",
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(), nullable=False),
        sa.Column("source_meeting_ids", postgresql.JSONB(), server_default="[]", nullable=False),
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
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id", "period_start"),
    )


def downgrade() -> None:
    op.drop_table("intel_reports")
    op.drop_index("ix_intel_predictions_team_id", table_name="intel_predictions")
    op.drop_table("intel_predictions")
    op.drop_index("ix_intel_alignment_team_id", table_name="intel_alignment")
    op.drop_table("intel_alignment")
    op.drop_index("ix_intel_gap_patterns_team_id", table_name="intel_gap_patterns")
    op.drop_table("intel_gap_patterns")
    op.drop_index("ix_intel_scores_team_id", table_name="intel_scores")
    op.drop_table("intel_scores")
    op.drop_table("intel_completion")
