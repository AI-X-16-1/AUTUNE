"""user_integrations

A person's own connection to an outside service -- their calendar (#435) --
beside the team's in ``team_integrations``. #59 decided that
per-person consent gets a new table rather than a wider ``team_integrations``,
because the lifetimes are opposite: a team connection outlives whoever made it,
a person's grant is deleted with them (``ON DELETE CASCADE`` on ``user_id``).

``secret`` holds Fernet ciphertext; ``config`` is JSONB for the same reason as
the team table.

See docs/architecture/data-model.md.

Revision ID: 3b8e6f1c9a47
Revises: 9235e57e02fe
Create Date: 2026-09-29 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3b8e6f1c9a47"
# After #425's google identity revision, not beside it: both used to hang off
# team_integrations, which gave core two heads (review of #444).
down_revision: str | None = "9235e57e02fe"  # core: add_google_identity_to_users
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_integrations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("service", sa.String(length=32), nullable=False),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # The grant is the person's: it goes when they do.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "service", name="uq_user_integrations_user_service"),
        sa.CheckConstraint("service IN ('calendar')", name="ck_user_integrations_service"),
    )
    op.create_index(
        op.f("ix_user_integrations_user_id"), "user_integrations", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_integrations_user_id"), table_name="user_integrations")
    op.drop_table("user_integrations")
