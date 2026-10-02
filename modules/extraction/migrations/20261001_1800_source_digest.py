"""source_digest and needs_recheck on items and decisions: a corrected transcript reaches B

A PII report (S30, #584) masks stored utterances again and republishes
``TranscriptReady`` without saying which lines changed. B keeps a digest of the
masked text an item or decision was drawn from (sha256, not reversible -- the
#518 consent-key pattern), compares it on every run, and fixes or flags what
changed (#586). ``needs_recheck`` marks what a person should look at again:
a summary rewritten from the corrected line, or their own wording, which B cannot
correct for them. Both nullable / false for existing rows: the first run records
a baseline and changes nothing.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 7b2e4c9a1f58
Revises: 6a1d3f8b2e47
Create Date: 2026-10-01 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b2e4c9a1f58"
down_revision: str | None = "6a1d3f8b2e47"  # extraction: calendar_cleanup
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("ext_action_items", "ext_decisions"):
        op.add_column(table, sa.Column("source_digest", sa.String(length=64), nullable=True))
        op.add_column(
            table,
            sa.Column("needs_recheck", sa.Boolean(), server_default=sa.false(), nullable=False),
        )


def downgrade() -> None:
    for table in ("ext_action_items", "ext_decisions"):
        op.drop_column(table, "needs_recheck")
        op.drop_column(table, "source_digest")
