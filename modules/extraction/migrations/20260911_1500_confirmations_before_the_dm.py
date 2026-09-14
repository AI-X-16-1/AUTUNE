"""ext_confirmations: record an ambiguous agreement before its DM goes out

``sent_at`` becomes nullable. The pipeline now writes a row for every utterance
it calls ambiguous; ``sent_at`` stays empty until the question actually reaches
the speaker, which cannot happen yet (#70, #30). A new check keeps an answer
from existing without a question.

Downgrade deletes the rows that were never asked before restoring NOT NULL.
Those rows are derived from classifications and are rebuilt by the pipeline;
the ones a DM went out for are kept.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 3df3e0a67a67
Revises: 5b1039d48fa9
Create Date: 2026-09-11 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3df3e0a67a67"
down_revision: str | None = "5b1039d48fa9"  # extraction: add_action_item_due_text
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "ext_confirmations",
        "sent_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_ext_confirmations_answer_needs_a_question",
        "ext_confirmations",
        "resolved_kind IS NULL OR sent_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ext_confirmations_answer_needs_a_question", "ext_confirmations", type_="check"
    )
    op.execute("DELETE FROM ext_confirmations WHERE sent_at IS NULL")
    op.alter_column(
        "ext_confirmations",
        "sent_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
