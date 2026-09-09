"""add google identity to users

Google sign-in (W2) needs a stable per-account identifier to recognise a
returning user, and a place to record the last sign-in. Both live on ``users``
rather than in a separate identities table — a second provider (Slack) gets its
own column here, and only a third would justify the extra table.

Revision ID: 9235e57e02fe
Revises: aad0ea392ddc
Create Date: 2026-09-09 11:14:39.016162
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9235e57e02fe"
down_revision: str | None = "aad0ea392ddc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("google_sub", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_users_google_sub", "users", ["google_sub"])


def downgrade() -> None:
    op.drop_constraint("uq_users_google_sub", "users", type_="unique")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "google_sub")
