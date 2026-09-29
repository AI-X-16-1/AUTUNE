"""ext_external_refs.site: the Jira site an issue key belongs to (#458)

An issue key like ``KAN-1`` is unique only within one Jira site. After a team
reconnects to another site, a stored key without its site would name an
unrelated issue there, and the next edit would overwrite and close it. The
cloud id is stored with the key; a key from another site is never written to.

Nullable: Notion refs have no site, and a Jira ref without one (made before
this column) is treated as another site's -- the item gets a new issue.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 9b3f1d7c2a58
Revises: 7c2e5a9d4b18
Create Date: 2026-09-29 23:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b3f1d7c2a58"
down_revision: str | None = "7c2e5a9d4b18"  # extraction: calendar_events
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ext_external_refs", sa.Column("site", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("ext_external_refs", "site")
