"""add ext_decisions and ext_decision_sources

Module D keys a decision lineage on ``ext_decisions.id``; a ``dec_`` id is what
a ``thr_`` thread points at. Pipeline step 5, see docs/modules/extraction.md.

Both tables reach deletion through meetings.id, and the sources table also
references utterances.id, so this depends on the core shared_entities revision
the same way the action-item revision does.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 7b2d5f014ce8
Revises: 4c1e8b90d3a7
Create Date: 2026-09-09 18:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b2d5f014ce8"
down_revision: str | None = "4c1e8b90d3a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "d34994600a9a"  # core: shared_entities


def upgrade() -> None:
    op.create_table(
        "ext_decisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_ext_decisions_confidence"
        ),
    )
    op.create_index("ix_ext_decisions_meeting_id", "ext_decisions", ["meeting_id"])

    op.create_table(
        "ext_decision_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("decision_id", sa.String(length=64), nullable=False),
        sa.Column("utterance_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["ext_decisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["utterance_id"], ["utterances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", "utterance_id", name="uq_ext_decision_sources"),
    )
    op.create_index("ix_ext_decision_sources_decision_id", "ext_decision_sources", ["decision_id"])
    op.create_index(
        "ix_ext_decision_sources_utterance_id", "ext_decision_sources", ["utterance_id"]
    )


def downgrade() -> None:
    op.drop_table("ext_decision_sources")
    op.drop_table("ext_decisions")
