"""gap_scorings: how often a meeting's rescore failed, and against which grouping

The periodic rescore (#415, #506) re-runs detection for a meeting whose
grouping of participants has moved. A meeting that keeps failing kept
disagreeing, so it was retried every ten minutes forever, and with a hosted
verifier each try spends the provider's daily quota (#516).

Three nullable-or-defaulted columns, so existing rows need no backfill:
``failed_people_key`` is the grouping the failures were against,
``rescore_failures`` how many in a row, and ``last_failed_at`` when the last
one was. A new grouping clears the count; a successful detection clears all
three.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: 6d3a9e2f1b84
Revises: f2b9d6e1a4c7
Create Date: 2026-09-30 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6d3a9e2f1b84"
down_revision: str | None = "f2b9d6e1a4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gap_scorings", sa.Column("failed_people_key", sa.String(length=64)))
    op.add_column(
        "gap_scorings",
        sa.Column("rescore_failures", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("gap_scorings", sa.Column("last_failed_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("gap_scorings", "last_failed_at")
    op.drop_column("gap_scorings", "rescore_failures")
    op.drop_column("gap_scorings", "failed_people_key")
