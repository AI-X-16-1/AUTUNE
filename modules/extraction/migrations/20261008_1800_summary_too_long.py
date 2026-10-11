"""ext_meeting_summaries.too_long: a meeting too long for a summary says so (#421 v2)

A meeting whose lines need more model calls than one meeting is allowed got no
summary, no row and no word on the tab; and since nothing recorded it, every
rerun over the same lines went through it again -- with the calls of a first
level spent, when the limit was passed at the second. The row now says so:
``too_long`` true, an empty overview, no points, the digest of the lines it
was found for. No text of the meeting is in such a row.

Existing rows are summaries that were written: ``false``.

Downgrading deletes the rows that say "too long" before dropping the column --
without it they would read as summaries with an empty overview.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 7a3c5e9d1f42
Revises: 5d1f8b3a7c46
Create Date: 2026-10-08 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7a3c5e9d1f42"
down_revision: str | None = "5d1f8b3a7c46"  # extraction: a deleted source forgets its excerpt
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ext_meeting_summaries",
        sa.Column("too_long", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.execute("DELETE FROM ext_meeting_summaries WHERE too_long")
    op.drop_column("ext_meeting_summaries", "too_long")
