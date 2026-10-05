"""user_integrations may hold a person's own Drive grant (#817)

A person can let Autune read the Drive files they pick for it in Google's own
file picker, so a file can be shown where it is talked about. The grant is
``drive.file`` -- the picked files and nothing else in their Drive -- and is a
service of its own, ``drive``.

See docs/architecture/data-model.md.

Revision ID: 3d8f1c6a9b27
Revises: f2a6b9d4c8e1
Create Date: 2026-10-05 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "3d8f1c6a9b27"
down_revision: str | None = "f2a6b9d4c8e1"  # core: team_members pinned_at
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_user_integrations_service", "user_integrations", type_="check")
    op.create_check_constraint(
        "ck_user_integrations_service",
        "user_integrations",
        "service IN ('calendar','slack','gmail_send','drive')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM user_integrations WHERE service = 'drive'")
    op.drop_constraint("ck_user_integrations_service", "user_integrations", type_="check")
    op.create_check_constraint(
        "ck_user_integrations_service",
        "user_integrations",
        "service IN ('calendar','slack','gmail_send')",
    )
