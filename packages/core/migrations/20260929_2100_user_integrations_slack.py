"""user_integrations may hold a person's own Slack identity (#255, #280)

A person links their Slack account once ("Sign in with Slack", ``openid``
only); the row keeps their Slack member id so a direct message to them can be
addressed. No token is kept -- the id is all a DM needs -- and nothing about
anyone else is read (#70).

See docs/architecture/data-model.md.

Revision ID: 5e1b8d3a6c20
Revises: 3b8e6f1c9a47
Create Date: 2026-09-29 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "5e1b8d3a6c20"
down_revision: str | None = "3b8e6f1c9a47"  # core: user_integrations
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_user_integrations_service", "user_integrations", type_="check")
    op.create_check_constraint(
        "ck_user_integrations_service", "user_integrations", "service IN ('calendar','slack')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM user_integrations WHERE service = 'slack'")
    op.drop_constraint("ck_user_integrations_service", "user_integrations", type_="check")
    op.create_check_constraint(
        "ck_user_integrations_service", "user_integrations", "service IN ('calendar')"
    )
