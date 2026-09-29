"""ext_notion_targets: the page and databases a team's Notion sync writes to (#428)

A one-click Notion connection stores the workspace token in team_integrations
(core); module B then makes its three databases under a page the team chose and
records them here, in its own table, rather than writing the settings layer's
config. One row per team, deleted with the team.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 4d9a2c7e1f35
Revises: 7c2e5a9d4b18
Create Date: 2026-09-29 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d9a2c7e1f35"
down_revision: str | None = "7c2e5a9d4b18"  # extraction: calendar_events
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_notion_targets",
        sa.Column(
            "team_id",
            sa.String(length=64),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("parent_page_id", sa.String(length=64), nullable=False),
        sa.Column("action_db_id", sa.String(length=64), nullable=False),
        sa.Column("decision_db_id", sa.String(length=64), nullable=False),
        sa.Column("minutes_db_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("ext_notion_targets")
