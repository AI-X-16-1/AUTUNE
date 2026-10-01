"""ext_external_refs.synced_category: the Jira status category Autune last saw

The Jira read-back compares an issue's status category with the one Autune
last left it in or last read, to tell a person's move in Jira from a board
edit that has not reached Jira yet. Nullable: refs written before it have
none, and the read-back records Jira's category as their baseline without
touching the board.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 2e8b4f6c1a57
Revises: 5c7e1a9d3b24
Create Date: 2026-09-30 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2e8b4f6c1a57"
down_revision: str | None = "5c7e1a9d3b24"  # extraction: meeting_notes
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ext_external_refs", sa.Column("synced_category", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ext_external_refs", "synced_category")
