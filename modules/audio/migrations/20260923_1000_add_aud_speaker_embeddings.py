"""add aud_speaker_embeddings

One table, two kinds of row: an observation of one speaker in one meeting
(cascades with the meeting) and a confirmed voice profile (lives on the user).
Identification, #6. Owner: 김민경.

``depends_on`` pins the core revision that creates ``meetings`` and ``users``.
Chains onto the audio branch's previous head, so the branch keeps one head.

Revision ID: 8f2b6d4a1c93
Revises: 5e7a1c9b2d40
Create Date: 2026-09-23 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "8f2b6d4a1c93"
down_revision: str | None = "5e7a1c9b2d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "d34994600a9a"  # core: shared_entities


def upgrade() -> None:
    # The extension is in the image (infra/docker-compose.yml) and module D's
    # revision creates it too; both are guarded, so whichever runs first wins.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "aud_speaker_embeddings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=True),
        sa.Column("speaker_label", sa.String(length=100), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("vector", Vector(256), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=False),
        sa.Column("source_meeting_id", sa.String(length=64), nullable=True),
        sa.Column("source_speaker_label", sa.String(length=100), nullable=True),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(meeting_id IS NOT NULL AND speaker_label IS NOT NULL AND user_id IS NULL)"
            " OR (meeting_id IS NULL AND speaker_label IS NULL AND user_id IS NOT NULL)",
            name="ck_aud_speaker_embeddings_observation_or_profile",
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_meeting_id"], ["meetings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_aud_speaker_embeddings_meeting_id", "aud_speaker_embeddings", ["meeting_id"]
    )
    op.create_index("ix_aud_speaker_embeddings_user_id", "aud_speaker_embeddings", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_aud_speaker_embeddings_user_id", table_name="aud_speaker_embeddings")
    op.drop_index("ix_aud_speaker_embeddings_meeting_id", table_name="aud_speaker_embeddings")
    op.drop_table("aud_speaker_embeddings")
