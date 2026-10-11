"""the day an event picked for the next meeting starts

"다음 회의 잡기" (#824) now keeps the start day of the event a gap's line
went onto (``gap_agenda_events.event_day``), so the Follow-up approval card
can offer the days people picked for the next meeting beside the day its rule
suggests (``tools.next_meeting_days``). That read also names who picked
each day, by the display name of the row's ``user_id`` while they are still
on the team; nothing else of the calendar or the event leaves the table.

Nullable, no backfill: the day was never stored before, and an old record's
event is read again only when somebody presses again.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: 9c4e7a2b5d18
Revises: 2f6b8d1e4a93
Create Date: 2026-10-08 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c4e7a2b5d18"
down_revision: str | None = "2f6b8d1e4a93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gap_agenda_events", sa.Column("event_day", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("gap_agenda_events", "event_day")
