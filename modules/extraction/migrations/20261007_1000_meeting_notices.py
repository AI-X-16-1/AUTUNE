"""add ext_meeting_notices: that a person was told, right after a meeting, that work of it landed on them

One row a person and meeting: the primary key is the "once". No text and no
count -- the message says how many drafts wait for the person and links to
them, and is not kept. A row says the notice was sent **or refused**: a
notice the outbound check refused keeps its row too, so that it is reported
once and not built again, and nothing on the row tells the two apart. Goes
with the meeting -- its retention expiry included -- and with the person.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 5c8f2a7d1e94
Revises: 8e5c1a7d3f94
Create Date: 2026-10-07 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c8f2a7d1e94"
down_revision: str | None = "8e5c1a7d3f94"  # extraction: work reports (#954)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_meeting_notices",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "user_id"),
    )
    op.create_index("ix_ext_meeting_notices_user_id", "ext_meeting_notices", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ext_meeting_notices_user_id", table_name="ext_meeting_notices")
    op.drop_table("ext_meeting_notices")
