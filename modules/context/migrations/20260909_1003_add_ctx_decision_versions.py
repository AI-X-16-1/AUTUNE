"""add ctx_decision_versions

One version of a decision, as seen in one meeting, chained onto the previous one.
``source_decision_id`` (``dec_``) is B's, stored without a constraint.
``previous_meeting_id`` is unconstrained on purpose: a retention sweep on that
meeting must not cascade into this thread. Owner: 문민재.

Revision ID: e4f3a5b6c7d8
Revises: d3e2f4a5b6c7
Create Date: 2026-09-09 10:03:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e4f3a5b6c7d8"
down_revision: str | None = "d3e2f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ctx_decision_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("source_decision_id", sa.String(length=64), nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("previous_version_id", sa.Integer(), nullable=True),
        sa.Column("current_statement", sa.Text(), nullable=False),
        sa.Column("previous_statement", sa.Text(), nullable=True),
        sa.Column("previous_meeting_id", sa.String(length=64), nullable=True),
        sa.Column("change_type", sa.String(length=16), nullable=False),
        sa.Column("nli_label", sa.String(length=16), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "key_stakeholders_absent", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("nli_version", sa.String(length=200), nullable=False),
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
        sa.CheckConstraint(
            "change_type IN ('unchanged', 'modified', 'reversed', 'new')",
            name="ck_ctx_decision_versions_change_type",
        ),
        sa.CheckConstraint(
            "nli_label IS NULL OR nli_label IN ('entailment', 'contradiction', 'neutral')",
            name="ck_ctx_decision_versions_nli_label",
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["ctx_decisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["previous_version_id"], ["ctx_decision_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ctx_decision_versions_thread_id",
        "ctx_decision_versions",
        ["thread_id"],
        unique=False,
    )
    op.create_index(
        "ix_ctx_decision_versions_meeting_id",
        "ctx_decision_versions",
        ["meeting_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ctx_decision_versions_meeting_id", table_name="ctx_decision_versions")
    op.drop_index("ix_ctx_decision_versions_thread_id", table_name="ctx_decision_versions")
    op.drop_table("ctx_decision_versions")
