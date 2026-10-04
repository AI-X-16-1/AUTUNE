"""add ext_projects and a project on each action item and decision

A team lists the projects it works on (the user, 2026-10-04): a name, other
names people say for it, and optionally its own Jira project. A meeting's items
and decisions each point at one, chosen from what was said or by a person, so a
meeting that covers several projects can be summarised and sent out per
project. Typed by a member, not derived from speech; goes with the team.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 5c2e8a4f1d63
Revises: 9d4e6b3f7a20
Create Date: 2026-10-04 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c2e8a4f1d63"
down_revision: str | None = "9d4e6b3f7a20"  # extraction: add_ext_due_reminders
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("ext_action_items", "ext_decisions")


def upgrade() -> None:
    op.create_table(
        "ext_projects",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("aliases", sa.Text(), nullable=False),
        sa.Column("jira_project_key", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "name", name="uq_ext_projects_team_name"),
    )
    op.create_index("ix_ext_projects_team_id", "ext_projects", ["team_id"])
    for table in _TABLES:
        op.add_column(table, sa.Column("project_id", sa.String(length=64), nullable=True))
        op.add_column(
            table,
            sa.Column("project_by_person", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.create_foreign_key(
            f"fk_{table}_project_id_ext_projects",
            table,
            "ext_projects",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_project_id", table_name=table)
        op.drop_constraint(f"fk_{table}_project_id_ext_projects", table, type_="foreignkey")
        op.drop_column(table, "project_by_person")
        op.drop_column(table, "project_id")
    op.drop_index("ix_ext_projects_team_id", table_name="ext_projects")
    op.drop_table("ext_projects")
