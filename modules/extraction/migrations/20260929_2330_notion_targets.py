"""ext_notion_targets: the page and databases a team's Notion sync writes to (#428)

A one-click Notion connection stores the workspace token in team_integrations
(core); module B then makes its three databases under a page the team chose and
records them here, in its own table, rather than writing the settings layer's
config. One row per team, deleted with the team.

``workspace_id`` is the Notion workspace the databases were made in. A team
that disconnects and connects another workspace keeps its row until it sets up
again; the row is ignored while it names another workspace than the current
connection's, so syncs never write to database ids the new token cannot see.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 4d9a2c7e1f35
Revises: 9b3f1d7c2a58
Create Date: 2026-09-29 23:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d9a2c7e1f35"
down_revision: str | None = "9b3f1d7c2a58"  # extraction: external_refs_site
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
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("ext_notion_targets")
