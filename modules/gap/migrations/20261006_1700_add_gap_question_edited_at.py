"""when a member rewrote a gap's question by hand

S20's 해소용 질문 can be edited (#824). ``question_edited_at`` marks a question
a person wrote, so a re-run (``service._store_gaps``) and
``refresh_questions`` keep it rather than recompute it. Nullable, no actor
column, the rules ``dismissed_at`` keeps.

No backfill: nobody has edited a question before this revision.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: 2f6b8d1e4a93
Revises: 7d2e9b4f1c60
Create Date: 2026-10-06 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2f6b8d1e4a93"
down_revision: str | None = "7d2e9b4f1c60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gap_gaps", sa.Column("question_edited_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("gap_gaps", "question_edited_at")
