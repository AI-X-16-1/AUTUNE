"""ext_meeting_notes: the team's memo on a meeting's summary tab (#421)

One row per meeting holding free text a member typed on S15's 요약 tab; a
blank memo is no row. No author column. Deleted with the meeting.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 5c7e1a9d3b24
Revises: 8f4b2d6a1c93
Create Date: 2026-09-30 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c7e1a9d3b24"
down_revision: str | None = "8f4b2d6a1c93"  # extraction: extraction_runs
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_meeting_notes",
        sa.Column(
            "meeting_id",
            sa.String(length=64),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ext_meeting_notes")
