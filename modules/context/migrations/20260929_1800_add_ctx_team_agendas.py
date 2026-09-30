"""add ctx_team_agendas

One row per team: the latest ``TeamAgenda`` module B published (#436), the open
Jira issues made from the team's action items, for the pre-meeting brief's
agenda. Replaced whole by a newer snapshot, ignored and deleted once B stops
refreshing it -- see ``autune_context.models.CtxTeamAgenda``. Owner: 문민재.

Revision ID: 95e8bad8de6c
Revises: 7b5d2880cba1
Create Date: 2026-09-29 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "95e8bad8de6c"
down_revision: str | None = "7b5d2880cba1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ctx_team_agendas",
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id"),
    )


def downgrade() -> None:
    op.drop_table("ctx_team_agendas")
