"""add ext_materials

A team keeps Google Drive files on its 자료 screen (#817; the user,
2026-10-08): a title a member typed, the file's id and which Google editor it
belongs to. Not the file and not the pasted link -- Autune reads nothing of
the file and holds no Drive permission. No column names a person; goes with
the team.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 4e8b1f6a2c97
Revises: 7a2d4c9e1b63
Create Date: 2026-10-08 01:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4e8b1f6a2c97"
down_revision: str | None = "7a2d4c9e1b63"  # extraction: edit event kind 'closed' (#856)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_materials",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("drive_file_id", sa.String(length=200), nullable=False),
        sa.Column("drive_kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "drive_kind IN ('file','document','presentation','spreadsheets')",
            name="ck_ext_materials_drive_kind",
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "drive_file_id", name="uq_ext_materials_team_file"),
    )
    op.create_index("ix_ext_materials_team_id", "ext_materials", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_ext_materials_team_id", table_name="ext_materials")
    op.drop_table("ext_materials")
