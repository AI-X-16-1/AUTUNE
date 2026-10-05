"""add ext_meeting_summaries: a meeting's summary written by a cloud model (#421 v2)

Only with ``AUTUNE_EXTRACTION_SUMMARY_IMPL=llm``. One row per meeting: an
overview, points one per line, the digest of the lines it was written from (a
summary of lines that have changed since is not shown) and the model that wrote
it. Meeting content, so it goes with the meeting (CASCADE) and keeps its
retention.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 3f7a1c9e5b28
Revises: 4d8e2a6c9f31
Create Date: 2026-10-04 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f7a1c9e5b28"
down_revision: str | None = "4d8e2a6c9f31"  # extraction: external cleanup
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_meeting_summaries",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("overview", sa.Text(), nullable=False),
        sa.Column("points", sa.Text(), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id"),
    )


def downgrade() -> None:
    op.drop_table("ext_meeting_summaries")
