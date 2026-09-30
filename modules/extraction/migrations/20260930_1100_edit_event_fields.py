"""ext_edit_events.fields: which fields an edit changed, never the values (#109)

The action-item drawer (S18) shows an item's history. #109 settled what it may
keep: no person (ADR 0003) and no value before the edit -- that would keep the
sentence a person chose to replace (privacy.md section 4). So an edit event
gains the names of the fields it changed, comma-separated and sorted. Rows
written before this have none.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 3a1f6c9e2b47
Revises: 7c2e5a9d4b18
Create Date: 2026-09-30 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3a1f6c9e2b47"
down_revision: str | None = "7c2e5a9d4b18"  # extraction: calendar_events
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ext_edit_events", sa.Column("fields", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("ext_edit_events", "fields")
