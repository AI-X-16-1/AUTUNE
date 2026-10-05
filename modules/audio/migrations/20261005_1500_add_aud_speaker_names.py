"""add aud_speaker_names

A name typed for a speaker with no account on the team, for one meeting only:
(meeting, label) -> name. No user id, no voice. Cascades with the meeting,
which is the only way it goes and the only one it needs.

Owner: 김민경.

Revision ID: f4a0d26b8c39
Revises: 5c1e9a7d3b20
Create Date: 2026-10-05 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a0d26b8c39"
down_revision: str | None = "5c1e9a7d3b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aud_speaker_names",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("speaker_label", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "speaker_label"),
    )


def downgrade() -> None:
    op.drop_table("aud_speaker_names")
