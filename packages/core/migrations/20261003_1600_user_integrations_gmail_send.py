"""user_integrations may hold a person's own Gmail send grant (#552)

A team member can have Autune mail an invitation link from their own Gmail
account. The grant is ``gmail.send`` only -- it can send as that person and
read nothing -- so it is a service of its own, ``gmail_send``, and not the
``gmail`` that #431 (reading a mailbox) has yet to decide.

See docs/architecture/data-model.md.

Revision ID: 7c2f9a4e1b85
Revises: e1f5a8c3d7b2
Create Date: 2026-10-03 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "7c2f9a4e1b85"
down_revision: str | None = "e1f5a8c3d7b2"  # core: users_sessions_valid_from
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_user_integrations_service", "user_integrations", type_="check")
    op.create_check_constraint(
        "ck_user_integrations_service",
        "user_integrations",
        "service IN ('calendar','slack','gmail_send')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM user_integrations WHERE service = 'gmail_send'")
    op.drop_constraint("ck_user_integrations_service", "user_integrations", type_="check")
    op.create_check_constraint(
        "ck_user_integrations_service", "user_integrations", "service IN ('calendar','slack')"
    )
