"""team_integrations

Integration credentials belong to a customer team, not to the deployment. Before
this table there was one Notion token and one Jira site per deployment, which is
enough for our own beta and nothing beyond it — screen S28 lets each team point
Autune at their own workspace.

On the core branch rather than a module's: four modules read this table (B for
Notion and Jira, D for Calendar, everyone for Slack) and none of them owns it.
It is written by ``autune_core``, the way ``users`` and ``teams`` are.

``secret`` holds Fernet ciphertext, so a database dump is not a set of working
tokens. ``config`` is JSONB because its shape differs per service — Notion has
three database ids, Jira has a project key and transition ids — and nothing
joins on it.

See docs/architecture/data-model.md.

Revision ID: 7c1f4b9e02a5
Revises: aad0ea392ddc
Create Date: 2026-09-09 01:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c1f4b9e02a5"
down_revision: str | None = "aad0ea392ddc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "team_integrations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("service", sa.String(length=32), nullable=False),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("connected_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        # The connection outlives the person who made it; only their name goes.
        sa.ForeignKeyConstraint(["connected_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "service", name="uq_team_integrations_team_service"),
        sa.CheckConstraint(
            "service IN ('notion','jira','slack','calendar')",
            name="ck_team_integrations_service",
        ),
    )
    op.create_index(
        op.f("ix_team_integrations_team_id"), "team_integrations", ["team_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_team_integrations_team_id"), table_name="team_integrations")
    op.drop_table("team_integrations")
