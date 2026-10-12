"""ext_material_alarms.told_at: when the team's approvers were told of a stopped upload

An upload refused for a confidentiality marking leaves one alarm row (#817).
The agent layer tells the team's approvers (#1201) by looking for rows nobody
was told of, once an hour, and this column is how a row is told once: null
until the approvers' message about it went, then the time it went.

It is a time and nothing else -- not who was messaged and not how many. It
goes when the row goes: on an approver's acknowledgement, or 30 days after
the row was written, told or not.

A row already in the table was told to nobody and stays null, so the first
run after this tells its team's approvers of it.

The downgrade drops the column; the code before this did not read it.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: a4c7e2b9d1f6
Revises: 7b3e9d2f5a61
Create Date: 2026-10-12 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c7e2b9d1f6"
down_revision: str | None = "7b3e9d2f5a61"  # extraction: material vectors
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ext_material_alarms", sa.Column("told_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ext_material_alarms", "told_at")
