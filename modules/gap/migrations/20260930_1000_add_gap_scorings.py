"""gap_scorings: who counted as one person when a meeting was last scored

A stored gap's risk reads the participation matrix merged by
``participants.user_id``, and module A fills that column when a speaker is
confirmed, after the gaps were scored. One row per meeting records a digest of
the grouping the scoring used, so the periodic rescore can find the meetings
whose grouping has moved since (#415).

No backfill. A meeting scored before this revision has no row and is left
alone until something re-runs its detection; speaker confirmation (#370)
landed days before this, so few such meetings have a ``user_id`` to be stale
about.

Chains onto the coverage revision, the gap branch's head on ``main``.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: f2b9d6e1a4c7
Revises: e4a7c81b6f30
Create Date: 2026-09-30 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2b9d6e1a4c7"
down_revision: str | None = "e4a7c81b6f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gap_scorings",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("people_key", sa.String(length=64), nullable=False),
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


def downgrade() -> None:
    op.drop_table("gap_scorings")
