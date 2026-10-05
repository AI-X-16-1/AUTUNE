"""users.sessions_valid_from

When a person last signed out. A session token issued before it is refused, so
signing out ends the session on the server and not only in the browser that
held the cookie (``autune_core.auth.end_sessions``).

Nullable with no default on purpose: a person who has never signed out has no
moment to compare with, so applying this signs nobody out.

Revision ID: e1f5a8c3d7b2
Revises: c4a7e2d9b316
Create Date: 2026-10-02 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f5a8c3d7b2"
down_revision: str | None = "c4a7e2d9b316"  # core: user_consents
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("sessions_valid_from", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "sessions_valid_from")
