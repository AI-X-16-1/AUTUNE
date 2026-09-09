"""add ext_confirmations

One row per ambiguous agreement the speaker was asked about. Keyed by
utterance_id: one utterance gets one question, and Slack retrying a click has to
land on the same row.

No outcome column. ``resolved`` / ``undecided`` / ``pending`` is derived from
sent_at and responded_at whenever it is read, so nothing has to run at the
24-hour deadline for the rule to hold. See #12 and ui-spec S19.

Depends on the core shared_entities revision: the table reaches deletion through
meetings.id and its primary key references utterances.id.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 9e4a2c71b3d6
Revises: 4c1e8b90d3a7
Create Date: 2026-09-09 20:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9e4a2c71b3d6"
down_revision: str | None = "4c1e8b90d3a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "d34994600a9a"  # core: shared_entities


def upgrade() -> None:
    op.create_table(
        "ext_confirmations",
        sa.Column("utterance_id", sa.String(length=64), nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_kind", sa.String(length=32), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["utterance_id"], ["utterances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("utterance_id"),
        sa.CheckConstraint(
            "resolved_kind IS NULL OR resolved_kind IN ('commitment','decision','concern')",
            name="ck_ext_confirmations_resolved_kind",
        ),
        # An answer is a kind and a time together. Half of one means a resolution
        # that cannot be dated or a date with no answer, and the outcome is
        # derived from both.
        sa.CheckConstraint(
            "(resolved_kind IS NULL) = (responded_at IS NULL)",
            name="ck_ext_confirmations_answer_is_whole",
        ),
    )
    op.create_index("ix_ext_confirmations_meeting_id", "ext_confirmations", ["meeting_id"])


def downgrade() -> None:
    op.drop_table("ext_confirmations")
