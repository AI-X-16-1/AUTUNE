"""cancelled status and heartbeat_at on aud_jobs

A worker that dies mid-job left its meeting analyzing forever, and a wrong
upload could not be stopped. ``heartbeat_at`` is how the API tells a dead
worker from a slow one; ``cancelled`` is what a person's cancel writes. Nothing
here is meeting content: a status and a timestamp.

Owner: 김민경.

Revision ID: 5c1e9a7d3b20
Revises: e3f9c15a7b28
Create Date: 2026-10-03 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c1e9a7d3b20"
down_revision: str | None = "e3f9c15a7b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = "status IN ('queued','running','done','failed','superseded')"
_AFTER = "status IN ('queued','running','done','failed','superseded','cancelled')"


def upgrade() -> None:
    op.add_column("aud_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("ck_aud_jobs_status", "aud_jobs", type_="check")
    op.create_check_constraint("ck_aud_jobs_status", "aud_jobs", _AFTER)


def downgrade() -> None:
    # A cancelled attempt is over; failed is the nearest status the old
    # constraint knows.
    op.execute("UPDATE aud_jobs SET status = 'failed' WHERE status = 'cancelled'")
    op.drop_constraint("ck_aud_jobs_status", "aud_jobs", type_="check")
    op.create_check_constraint("ck_aud_jobs_status", "aud_jobs", _BEFORE)
    op.drop_column("aud_jobs", "heartbeat_at")
