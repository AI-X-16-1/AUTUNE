"""user_consents

What a person agreed to -- the terms, the privacy policy and whatever else the
consent page lists -- one row per document and version. Signing in used to
count as agreeing; nothing recorded it.

The row is the person's: it goes when they do (``ON DELETE CASCADE``). One row
per ``(user_id, document, version)``, so agreeing twice is one agreement and
the time is the first.

See ``autune_core.consents`` and docs/architecture/data-model.md.

Revision ID: c4a7e2d9b316
Revises: 8e4d2b7a1c93
Create Date: 2026-10-02 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a7e2d9b316"
down_revision: str | None = "8e4d2b7a1c93"  # core: user_integrations_slack_member_unique
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_consents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("document", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "agreed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "document", "version", name="uq_user_consents_user_document_version"
        ),
    )
    op.create_index("ix_user_consents_user_id", "user_consents", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_consents_user_id", table_name="user_consents")
    op.drop_table("user_consents")
