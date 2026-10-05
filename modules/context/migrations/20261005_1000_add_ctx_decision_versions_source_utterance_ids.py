"""add ctx_decision_versions.source_utterance_ids

A version's ``current_statement`` is B's sentence for a decision, drawn from
utterances. When a person deletes their own speech (#587, #614) D has to find
the statements that came from it, and nothing here said which utterances a
statement came from: B's ``Decision.source_utterance_ids`` was read and dropped.

This stores those ids, not their text. Nullable, because rows written before
this revision have no record; on a person's deletion those are treated as
possibly theirs and let go (erring toward deleting more), until their meeting
is next extracted.

Owner: 문민재.

Revision ID: 3c7e1a9b5d20
Revises: 95e8bad8de6c
Create Date: 2026-10-05 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3c7e1a9b5d20"
down_revision: str | None = "95e8bad8de6c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ctx_decision_versions",
        sa.Column("source_utterance_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ctx_decision_versions", "source_utterance_ids")
