"""add intel_meeting_reports.edited_by and edited_at

A team member may edit a report draft on the dashboard before it is posted;
who did it and when are kept with the draft (10/2).

Revision ID: 7e1b4d8a6c25
Revises: 2a7d5c9e1f34
Create Date: 2026-10-02 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7e1b4d8a6c25"
down_revision: str | None = "2a7d5c9e1f34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "intel_meeting_reports",
        sa.Column("edited_by", sa.String(64), nullable=True),
    )
    op.add_column(
        "intel_meeting_reports",
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    # A per-person record: deleting the person clears it and keeps the report.
    op.create_foreign_key(
        "fk_intel_meeting_reports_edited_by_users",
        "intel_meeting_reports",
        "users",
        ["edited_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_intel_meeting_reports_edited_by_users", "intel_meeting_reports", type_="foreignkey"
    )
    op.drop_column("intel_meeting_reports", "edited_at")
    op.drop_column("intel_meeting_reports", "edited_by")
