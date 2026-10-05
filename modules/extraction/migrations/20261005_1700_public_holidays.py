"""add ext_public_holidays: the public holidays no digest goes on

One row a day, as Google's public calendar of Korea's holidays listed it at
the last read, with that read's time. Replaced whole on every read. Dates of
public record: nothing about a person, a team or a meeting.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 5e2a8c1f7d94
Revises: 1b7d4e9a6c52
Create Date: 2026-10-05 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e2a8c1f7d94"
down_revision: str | None = "1b7d4e9a6c52"  # extraction: daily digests and pauses
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_public_holidays",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("day"),
    )


def downgrade() -> None:
    op.drop_table("ext_public_holidays")
