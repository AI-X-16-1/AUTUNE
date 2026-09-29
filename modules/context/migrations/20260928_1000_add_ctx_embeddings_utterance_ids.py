"""add ctx_embeddings.utterance_ids

The re-ranker scored a new topic's full text against a past topic's *label* --
a kiwipiepy noun or two, often a single word like "결제" -- so a cross-encoder
that gives a real match 0.6 against the past segment's text gave it ~0.000
against the label, and almost nothing ever cleared
``link_confidence_threshold``. The past segment's text was never kept anywhere
this module could find it again.

This stores which utterances a topic segment was cut from, not their text: the
text stays in ``utterances`` (already PII-masked, already cascading with its
meeting), and the re-ranker reads it from there. Nullable, because rows written
before this revision have no record of their segment; those fall back to the
label until their meeting is re-linked.

Owner: 문민재.

Revision ID: 7696c4b2cfb5
Revises: d0e1f2a3b4c5
Create Date: 2026-09-28 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7696c4b2cfb5"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ctx_embeddings",
        sa.Column("utterance_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ctx_embeddings", "utterance_ids")
