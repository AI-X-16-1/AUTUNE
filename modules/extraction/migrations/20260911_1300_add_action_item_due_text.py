"""add ext_action_items.due_text

The phrase a model item's due date was read from ("다음 주 금요일"), for S18 to
show beside the date. Nullable: hand-added items and items with no date phrase
have none, and a person setting the date clears it. See #11.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 5b1039d48fa9
Revises: 5ca253ba5379
Create Date: 2026-09-11 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b1039d48fa9"
down_revision: str | None = "5ca253ba5379"  # extraction: add_classifications
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ext_action_items", sa.Column("due_text", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("ext_action_items", "due_text")
