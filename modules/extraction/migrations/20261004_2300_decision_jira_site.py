"""add ext_decision_refs.site: the Jira site a decision's issue is on

A confirmed decision now also becomes a Jira issue (``jira_sync.sync_decision_to_jira``),
recorded in ext_decision_refs with system 'jira' -- which the table's check
already allows. A Jira key is unique only within a site, so the row keeps the
site's cloud id beside it, as ext_external_refs does for action items.
Nullable: a Notion page has no site. No text.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 7c3e9a1d5b42
Revises: 6d4b2e8f1a37
Create Date: 2026-10-04 23:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c3e9a1d5b42"
down_revision: str | None = "6d4b2e8f1a37"  # extraction: weekly digests
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ext_decision_refs", sa.Column("site", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("ext_decision_refs", "site")
