"""ext_material_chunks.embedding: a vector for each piece of an upload's masked text

A team searches its uploaded materials (#817). Each piece of masked text gets
a ``vector(1024)`` (KURE-v1's width) under an HNSW index for cosine distance,
and ``ext_materials.embedding_model`` names the embedder that made an
upload's vectors, so a search compares a question only with vectors of the
same model. The vector is computed from the masked text alone, in the upload
request; it lives in this column and nowhere else, so a member's delete of a
material takes its vectors with its text (``ON DELETE CASCADE``, as before).

Both columns are nullable: a piece stored before this revision has no vector
and is not found by a search. Upload has been off by default everywhere
(``AUTUNE_EXTRACTION_MATERIAL_UPLOAD``), so there should be none.

``depends_on`` names the core revision that enables pgvector, as module D's
``ctx_embeddings`` does, so the extension exists whichever branch runs first.
The downgrade drops the vectors and the model name; the masked text stays.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 7b3e9d2f5a61
Revises: 5d1a7c3e9b24
Create Date: 2026-10-10 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "7b3e9d2f5a61"
down_revision: str | None = "5d1a7c3e9b24"  # extraction: material uploads
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "aad0ea392ddc"  # core: enable_pgvector


def upgrade() -> None:
    op.add_column("ext_materials", sa.Column("embedding_model", sa.String(200), nullable=True))
    op.add_column("ext_material_chunks", sa.Column("embedding", Vector(1024), nullable=True))
    # Approximate nearest neighbours by cosine distance. Here and not in the
    # model: autogenerate does not represent it.
    op.execute(
        "CREATE INDEX ix_ext_material_chunks_embedding_hnsw ON ext_material_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_ext_material_chunks_embedding_hnsw", table_name="ext_material_chunks")
    op.drop_column("ext_material_chunks", "embedding")
    op.drop_column("ext_materials", "embedding_model")
