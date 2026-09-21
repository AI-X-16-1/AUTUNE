"""the coverage state a gap was raised from

``gap_gaps`` recorded that an item went unfilled but not how far the meeting
got with it. The S20 rail shows the checklist beside the gap list — covered,
partial, missing — and the difference between the last two is the difference
between "named it and moved on" and "never came up", which is a different
sentence to a reader and a different score to ``detect``.

Nullable, like the other three template columns: a gap found from the graph
alone was never compared against an item. The check constraint allows only
``partial`` and ``missing``, because a covered item raises no gap at all and a
row saying otherwise would be a bug rather than a state.

Backfill is deliberately absent. The rows in front of this revision carry the
coverage in their title wording only, and parsing product copy to recover a
state is a worse guess than saying nothing; the next pipeline run rewrites
every template row in place (``service._store_gaps``).

Chains onto the template-selection revision.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: e4a7c81b6f30
Revises: c9e5ab13d742
Create Date: 2026-09-21 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4a7c81b6f30"
down_revision: str | None = "c9e5ab13d742"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gap_gaps", sa.Column("coverage", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "ck_gap_gaps_coverage",
        "gap_gaps",
        "coverage IS NULL OR coverage IN ('partial', 'missing')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_gap_gaps_coverage", "gap_gaps", type_="check")
    op.drop_column("gap_gaps", "coverage")
