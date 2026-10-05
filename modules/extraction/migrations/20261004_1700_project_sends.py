"""add ext_project_sends, its cleanup queue and its refresh queue: where a project's minutes went

One row per meeting, project and tool (notion, slack, jira): the page id, the
Slack message as channel:ts, or the issue key, so sending again updates that
copy rather than making another, and a digest of the minutes it last received,
so a refresh leaves an unchanged copy alone. Addresses and a hash, no text.
Goes with the meeting and with the project (CASCADE).

ext_project_send_cleanup holds the copies still to retract after their meeting
or project was deleted: team, tool and address, no text. Goes with the team.

ext_project_refresh_owed holds the meetings whose copies still have to be
rewritten after a change -- a refresh failed, or speech was deleted -- for the
periodic retry: a meeting id and a count. Goes with the meeting.

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
        sa.Column("content_digest", sa.String(length=64), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["ext_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "project_id", "target"),
    )
    op.create_table(
        "ext_project_send_cleanup",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "target", "external_id", name="uq_ext_project_send_cleanup"),
    )
    op.create_index("ix_ext_project_send_cleanup_team_id", "ext_project_send_cleanup", ["team_id"])
    op.create_table(
        "ext_project_refresh_owed",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id"),
    )


def downgrade() -> None:
    op.drop_table("ext_project_refresh_owed")
    op.drop_index("ix_ext_project_send_cleanup_team_id", table_name="ext_project_send_cleanup")
    op.drop_table("ext_project_send_cleanup")
    op.drop_table("ext_project_sends")
