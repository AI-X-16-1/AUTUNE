"""add ext_materials and ext_material_chunks: a meeting's material as masked text with a vector a chunk

A document a team brought to a meeting, kept as masked chunks, each with a
``vector(1024)`` (KURE-v1) under an HNSW index for cosine distance. Deleted
with the meeting. Nothing registers a material yet (#817): this is the store.

``depends_on`` names the core revision that enables pgvector, as module D's
``ctx_embeddings`` does, so the extension exists whichever branch runs first.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 3c8e5a1d9f47
Revises: 9a4c2e7b1f63
Create Date: 2026-10-06 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "3c8e5a1d9f47"
down_revision: str | None = "9a4c2e7b1f63"  # extraction: extraction attempts
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "aad0ea392ddc"  # core: enable_pgvector


def upgrade() -> None:
    op.create_table(
        "ext_materials",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=400), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_ref", sa.String(length=400), nullable=True),
        sa.Column("registered_by", sa.String(length=64), nullable=True),
        sa.Column("model_version", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('drive', 'upload')", name="ck_ext_materials_source"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["registered_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ext_materials_meeting_id", "ext_materials", ["meeting_id"], unique=False)
    op.create_table(
        "ext_material_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("material_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["ext_materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("material_id", "position", name="uq_ext_material_chunks_position"),
    )
    op.create_index(
        "ix_ext_material_chunks_material_id", "ext_material_chunks", ["material_id"], unique=False
    )
    # Approximate nearest neighbours by cosine distance. Here and not in the
    # model: autogenerate does not represent it.
    op.execute(
        "CREATE INDEX ix_ext_material_chunks_embedding_hnsw ON ext_material_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_ext_material_chunks_embedding_hnsw", table_name="ext_material_chunks")
    op.drop_index("ix_ext_material_chunks_material_id", table_name="ext_material_chunks")
    op.drop_table("ext_material_chunks")
    op.drop_index("ix_ext_materials_meeting_id", table_name="ext_materials")
    op.drop_table("ext_materials")
