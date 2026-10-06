"""ext_decisions.statement_resolved: a model's sentence about a decision is marked where it is stored

`forget_speech` keeps a decision's statement when a model wrote it -- the
team's record -- and replaces it when it is the speaker's own line tidied.
Which of the two a statement is was read off `ext_decision_related`: a
write-up cited a line. A sentence the classifier writes with the label need
not cite one, so the mark is now its own column, set when the statement is
stored (`service.build_decisions`).

Existing rows keep exactly what they had: a decision with a cited line is
marked, every other is not.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 4d7b1e9a2c58
Revises: 9a4c2e7b1f63
Create Date: 2026-10-06 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d7b1e9a2c58"
down_revision: str | None = "9a4c2e7b1f63"  # extraction: extraction attempts
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ext_decisions",
        sa.Column("statement_resolved", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(
        "UPDATE ext_decisions SET statement_resolved = true "
        "WHERE origin = 'model' AND EXISTS ("
        "SELECT 1 FROM ext_decision_related r WHERE r.decision_id = ext_decisions.id)"
    )


def downgrade() -> None:
    op.drop_column("ext_decisions", "statement_resolved")
