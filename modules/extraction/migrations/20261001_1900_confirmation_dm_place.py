"""ext_confirmations.dm_channel, dm_ts, dm_digest: a corrected line reaches the DM that quoted it

A PII report masks a stored line again; a confirmation DM sent before it still
quotes the old text. Keeping where the DM landed (Slack's ``D...`` conversation
and the message ``ts``) and a digest of what it quoted lets B replace it in
place with ``chat.update`` (#586). Nullable: a DM sent before this was kept
cannot be corrected.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 8c3f5a2d6e19
Revises: 9a3c5e7b1d24
Create Date: 2026-10-01 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c3f5a2d6e19"
down_revision: str | None = "9a3c5e7b1d24"  # extraction: jira_pulled_at
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ext_confirmations", sa.Column("dm_channel", sa.String(length=32), nullable=True))
    op.add_column("ext_confirmations", sa.Column("dm_ts", sa.String(length=32), nullable=True))
    op.add_column("ext_confirmations", sa.Column("dm_digest", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("ext_confirmations", "dm_digest")
    op.drop_column("ext_confirmations", "dm_ts")
    op.drop_column("ext_confirmations", "dm_channel")
