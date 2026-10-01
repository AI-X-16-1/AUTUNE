"""ext_external_refs.pulled_at: when the Jira read-back last read the issue

The read-back reads a bounded number of issues per team per run. Ordered by
this, least recently read first, a team with more issues than that is read
over several runs instead of the newest ones every time (review of #548).
Nullable: an issue never read comes first.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 9a3c5e7b1d24
Revises: 3b8d6f1a9c42
Create Date: 2026-10-01 01:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a3c5e7b1d24"
down_revision: str | None = "3b8d6f1a9c42"  # extraction: action_item_agent_origins
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ext_external_refs", sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ext_external_refs", "pulled_at")
