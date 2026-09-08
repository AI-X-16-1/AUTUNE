"""enable pgvector

Embeddings live in PostgreSQL rather than a separate vector database, so this
extension has to exist before any module can create a vector column.

It is on the core branch, not a module's, because CREATE EXTENSION is
database-level and shared. Module D's embedding table chains onto this.

See docs/decisions/0004-pgvector-over-chroma.md.

Revision ID: aad0ea392ddc
Revises: d34994600a9a
Create Date: 2026-09-08 17:36:58.890266
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "aad0ea392ddc"
down_revision: str | None = "d34994600a9a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Dropping the extension would drop every vector column with it, so this
    # only removes it when nothing depends on it.
    op.execute("DROP EXTENSION IF EXISTS vector")
