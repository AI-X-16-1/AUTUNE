"""when a gap was sent on to the next meeting

S20's "다음 회의 어젠다로" had nowhere to write (#824). ``carried_at`` is the
mark, beside ``dismissed_at`` and with its rules: nullable, no actor column,
kept by a re-run that updates the row in place (``service._store_gaps``).

No meeting is named. A team's next meeting has no agenda to hold the gap yet
(#756), so the mark is what the next meeting's picture reads
(``tools.carried_gaps``).

No backfill: nobody has sent a gap on before this revision.

Chains onto the scoring-failures revision, the gap branch's head on ``main``.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: 9b2e4c7a1d53
Revises: 6d3a9e2f1b84
Create Date: 2026-10-05 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b2e4c7a1d53"
down_revision: str | None = "6d3a9e2f1b84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gap_gaps", sa.Column("carried_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("gap_gaps", "carried_at")
