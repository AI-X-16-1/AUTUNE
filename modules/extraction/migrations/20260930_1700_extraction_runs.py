"""ext_extraction_runs: which speech a meeting's last extraction read (#518)

One row per meeting with a digest of the ids of the utterances whose speaker
had consented when B last extracted it. The consent sweep compares it with the
consent as it is now and extracts again where the two differ. Deleted with the
meeting.

No backfill: a meeting extracted before this table has no row, and the sweep
leaves it alone until its next extraction writes one.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 8f4b2d6a1c93
Revises: 4d9a2c7e1f35
Create Date: 2026-09-30 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f4b2d6a1c93"
down_revision: str | None = "4d9a2c7e1f35"  # extraction: notion_targets
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_extraction_runs",
        sa.Column(
            "meeting_id",
            sa.String(length=64),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("consent_key", sa.String(length=64), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ext_extraction_runs")
