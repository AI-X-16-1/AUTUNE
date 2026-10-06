"""add ext_extraction_attempts: a failed extraction is counted, and a request to run it again waits here

One row per meeting whose extraction raised, or that a person asked to extract
again: how many runs in a row failed, the class of the last error, when, when
the team's Slack channel was told, and the request flag the worker takes.
Deleted with the meeting. Nothing about a person.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 9a4c2e7b1f63
Revises: 5e2a8c1f7d94
Create Date: 2026-10-06 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a4c2e7b1f63"
down_revision: str | None = "5e2a8c1f7d94"  # extraction: public holidays
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_extraction_attempts",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reason", sa.String(length=80), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("told_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id"),
    )


def downgrade() -> None:
    op.drop_table("ext_extraction_attempts")
