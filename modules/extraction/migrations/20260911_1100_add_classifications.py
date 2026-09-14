"""add ext_classifications

One row per utterance the classifier gave a kind, keyed by utterance_id so a
reprocessed meeting replaces its rows instead of adding a second set. An
utterance the model calls ``none`` has no row (#149).

Depends on the core shared_entities revision: the table reaches deletion through
meetings.id and its primary key references utterances.id.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 5ca253ba5379
Revises: 7b2d5f014ce8
Create Date: 2026-09-11 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5ca253ba5379"
down_revision: str | None = "7b2d5f014ce8"  # extraction: add_decisions
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "d34994600a9a"  # core: shared_entities


def upgrade() -> None:
    op.create_table(
        "ext_classifications",
        sa.Column("utterance_id", sa.String(length=64), nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=False),
        sa.Column("nli_verified", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["utterance_id"], ["utterances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("utterance_id"),
        # Five kinds and no "none": an utterance that is none of them has no row.
        sa.CheckConstraint(
            "kind IN ('commitment','decision','open_question','concern','ambiguous')",
            name="ck_ext_classifications_kind",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_ext_classifications_confidence"
        ),
    )
    op.create_index("ix_ext_classifications_meeting_id", "ext_classifications", ["meeting_id"])


def downgrade() -> None:
    op.drop_table("ext_classifications")
