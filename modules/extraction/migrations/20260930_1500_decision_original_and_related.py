"""ext_decisions.original_statement and ext_decision_related

Two changes for one reason. A decision's ``statement`` is now the line a person
sees and that leaves (noun-ended, or a model's summary), so the sentence *as it
was before* is kept in ``original_statement`` -- that is what module D is sent,
because D compares statements against a similarity threshold tuned on it. And
``ext_decision_related`` keeps the lines a summary says it was written from, so
the screen can show them beneath it.

Nullable column, no default: a decision a person typed has no original, and a row
from before this revision reads as ``original_statement or statement``.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 7e3f0b5c8d26
Revises: 6d2e9a4b7c15
Create Date: 2026-09-30 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7e3f0b5c8d26"
down_revision: str | None = "6d2e9a4b7c15"  # extraction: action item related lines
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ext_decisions", sa.Column("original_statement", sa.Text(), nullable=True))
    op.create_table(
        "ext_decision_related",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("decision_id", sa.String(length=64), nullable=False),
        sa.Column("utterance_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["ext_decisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["utterance_id"],
            ["utterances.id"],
            name="fk_ext_decision_related_utt",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", "utterance_id", name="uq_ext_decision_related"),
    )
    op.create_index(
        op.f("ix_ext_decision_related_decision_id"), "ext_decision_related", ["decision_id"]
    )
    op.create_index(
        op.f("ix_ext_decision_related_utterance_id"), "ext_decision_related", ["utterance_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ext_decision_related_utterance_id"), table_name="ext_decision_related")
    op.drop_index(op.f("ix_ext_decision_related_decision_id"), table_name="ext_decision_related")
    op.drop_table("ext_decision_related")
    op.drop_column("ext_decisions", "original_statement")
