"""add intel_meeting_reports correction columns

A member's correction to a posted report, sent as a reply under the post once
approved (10/2, #674): its text and id, who wrote it and when, the claim time,
and where it landed.

Revision ID: 9d2f6b3e8a41
Revises: 7e1b4d8a6c25
Create Date: 2026-10-02 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d2f6b3e8a41"
down_revision: str | None = "7e1b4d8a6c25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("intel_meeting_reports", sa.Column("correction_body", sa.Text(), nullable=True))
    op.add_column("intel_meeting_reports", sa.Column("correction_id", sa.String(64), nullable=True))
    op.add_column("intel_meeting_reports", sa.Column("corrected_by", sa.String(64), nullable=True))
    op.add_column(
        "intel_meeting_reports",
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "intel_meeting_reports",
        sa.Column("correction_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "intel_meeting_reports",
        sa.Column("correction_slack_ts", sa.String(64), nullable=True),
    )
    # A per-person record: deleting the person clears it and keeps the correction.
    op.create_foreign_key(
        "fk_intel_meeting_reports_corrected_by_users",
        "intel_meeting_reports",
        "users",
        ["corrected_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_intel_meeting_reports_corrected_by_users",
        "intel_meeting_reports",
        type_="foreignkey",
    )
    for column in (
        "correction_slack_ts",
        "correction_sent_at",
        "corrected_at",
        "corrected_by",
        "correction_id",
        "correction_body",
    ):
        op.drop_column("intel_meeting_reports", column)
