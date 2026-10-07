"""ext_notification_pauses.calendar_event_id: the event a person asked for on their own calendar

A person who sets their leave dates can tick "내 Google 캘린더에도 추가" (the
user, 2026-10-06). The event Autune then writes on that person's own calendar
is kept by id on the pause itself, so a changed range moves the same event and
a cleared one removes it. The id only -- no text, no dates beyond the two the
row already has.

``calendar_claimed_at`` beside it is when a save went to the calendar and has
not come back. Google is asked with no transaction open, so a second save is
kept out by this mark rather than by the row's lock. ``NULL`` at rest.

Existing rows have no event: nobody was offered the box before this.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 6e3b8d1f5a27
Revises: 4d7b1e9a2c58
Create Date: 2026-10-06 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6e3b8d1f5a27"
down_revision: str | None = "4d7b1e9a2c58"  # extraction: decision statement resolved
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ext_notification_pauses",
        sa.Column("calendar_event_id", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "ext_notification_pauses",
        sa.Column("calendar_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ext_notification_pauses", "calendar_claimed_at")
    op.drop_column("ext_notification_pauses", "calendar_event_id")
