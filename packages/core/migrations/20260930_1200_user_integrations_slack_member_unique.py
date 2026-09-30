"""user_integrations: one Slack member is linked to one Autune person (#478)

A second confirmed link to the same Slack member is a Slack session borrowed in
a shared browser, and it would send one person's speaking-ratio DM to another
(invariant 11). The confirm route checks for it first; this partial unique
index is what holds when two confirmations race. Only confirmed links count --
a pending one keeps its member under ``pending_slack_user_id``.

See docs/architecture/data-model.md.

Revision ID: 8e4d2b7a1c93
Revises: 5e1b8d3a6c20
Create Date: 2026-09-30 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e4d2b7a1c93"
down_revision: str | None = "5e1b8d3a6c20"  # core: user_integrations_slack
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_user_integrations_slack_member",
        "user_integrations",
        [sa.text("(config->>'slack_user_id')")],
        unique=True,
        postgresql_where=sa.text("service = 'slack' AND (config->>'slack_user_id') IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_user_integrations_slack_member", table_name="user_integrations")
