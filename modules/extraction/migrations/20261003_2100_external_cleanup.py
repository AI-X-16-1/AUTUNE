"""ext_external_cleanup: a deleted item's Notion page or Jira issue still owed a cleanup

Deleting an item trashes its Notion page and closes its Jira issue in the
deleting request, best effort. When that call fails, the item's row and its
``ext_external_refs`` row go anyway, and nothing knew the page or issue any
more -- it stayed live holding the item's sentence (#692). The deleting request
now records here what it could not clean up, and a periodic task retries it.

Ids only: the team, the system, the page id or issue key, and for Jira the
site the key is from. Nothing the item said. The team cascades, so a deleted
team takes its rows with it.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 4d8e2a6c9f31
Revises: b8e4f1a6c2d9
Create Date: 2026-10-03 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d8e2a6c9f31"
down_revision: str | None = "b8e4f1a6c2d9"  # extraction: sync retries
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_external_cleanup",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("system", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("site", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_ext_external_cleanup_team",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "system", "external_id", name="uq_ext_external_cleanup"),
        sa.CheckConstraint("system IN ('notion','jira')", name="ck_ext_external_cleanup_system"),
    )
    op.create_index(
        "ix_ext_external_cleanup_team_id", "ext_external_cleanup", ["team_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ext_external_cleanup_team_id", table_name="ext_external_cleanup")
    op.drop_table("ext_external_cleanup")
