"""ext_material_settings: whether a team keeps its uploads until it deletes them

An uploaded material (#817) keeps the team's retention window, counted from
the upload (``teams.retention_days``, 90 days by default); an hourly task
deletes the masked text of an upload past it. A team may switch, in its
settings, to keeping uploads until a member deletes them (the owner,
2026-10-11, after the review of #1198). One row per team that has ever
switched: ``keep_until_deleted`` and when it last changed. No column names a
person -- any member changes it, and who did is not recorded.

No row means the default: the window applies. Goes with the team
(``ON DELETE CASCADE``).

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 3b8f1d6a2c47
Revises: 5d1a7c3e9b24
Create Date: 2026-10-11 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3b8f1d6a2c47"
down_revision: str | None = "5d1a7c3e9b24"  # extraction: uploaded materials
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_material_settings",
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("keep_until_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id"),
    )


def downgrade() -> None:
    op.drop_table("ext_material_settings")
