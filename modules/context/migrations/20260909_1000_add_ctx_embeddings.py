"""add ctx_embeddings

Topic (and, in Phase 2, material) embeddings for cross-meeting retrieval.
``embedding`` is ``vector(1024)`` — KURE-v1 inherits bge-m3's dimension. Changing
the model to a different dimension is a new migration, not a config edit; see
autune_context.constants.EMBEDDING_DIM.

The ``vector`` extension is enabled by a packages/core migration; this only uses
it. Owner: 문민재.

Revision ID: b1c0d2e3f4a5
Revises: aea84543c9c3
Create Date: 2026-09-09 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "b1c0d2e3f4a5"
down_revision: str | None = "aea84543c9c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ctx_embeddings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ref_label", sa.String(length=400), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('topic', 'material')", name="ck_ctx_embeddings_kind"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ctx_embeddings_meeting_id", "ctx_embeddings", ["meeting_id"], unique=False)
    # Approximate nearest-neighbour index for cosine similarity. Built here rather
    # than in the model because SQLAlchemy autogenerate does not represent it.
    op.execute(
        "CREATE INDEX ix_ctx_embeddings_embedding_hnsw ON ctx_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_ctx_embeddings_embedding_hnsw", table_name="ctx_embeddings")
    op.drop_index("ix_ctx_embeddings_meeting_id", table_name="ctx_embeddings")
    op.drop_table("ctx_embeddings")
