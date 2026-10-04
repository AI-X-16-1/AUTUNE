"""add ext_project_sends: where a project's minutes for a meeting were sent

One row per meeting, project and tool (notion, slack, jira): the page id, the
Slack message as channel:ts, or the issue key, so sending again updates that
copy rather than making another. Addresses only, no text. Goes with the
meeting and with the project (CASCADE).

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 8e1b6d3a9f45
Revises: 5c2e8a4f1d63
Create Date: 2026-10-04 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e1b6d3a9f45"
down_revision: str | None = "5c2e8a4f1d63"  # extraction: projects
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_project_sends",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["ext_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "project_id", "target"),
    )


def downgrade() -> None:
    op.drop_table("ext_project_sends")
