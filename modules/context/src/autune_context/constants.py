"""Constants shared between the schema and the pipeline.

Kept apart from both so ``models`` (SQLAlchemy) and ``pipeline`` (model clients)
can import them without importing each other.
"""

from __future__ import annotations

EMBEDDING_DIM = 1024
"""Dimension of ``ctx_embeddings.embedding``. KURE-v1 inherits bge-m3's 1024.

Fixed in the database by the ``ctx_embeddings`` migration. Swapping in an
embedding model of a different dimension is a new migration, not a config edit —
``autune_context.pipeline.get_embedder`` refuses to start on a mismatch.
"""
