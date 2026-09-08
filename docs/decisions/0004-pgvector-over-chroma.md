# 0004. Embeddings live in PostgreSQL, not a separate vector database

**Status:** Accepted
**Date:** 2026-09-08
**Deciders:** the whole team

## Context

Module D retrieves past meetings and uploaded material by embedding similarity.
The original plan named Chroma, chosen early and without comparison.

Two constraints turned out to matter more than raw vector performance.

**Deletion has to be exact.** `../architecture/privacy.md` requires every
module's derived data to disappear when a meeting is deleted or its retention
window passes. An embedding outliving its meeting is a retention violation, not
a stale cache.

**Retrieval is always filtered.** D never searches all embeddings; it searches
within a team, often within a date range, and never across meetings a user
cannot see. Those filters are columns in PostgreSQL.

MVP scale is modest: thousands of meetings, tens of topics each — hundreds of
thousands of vectors, not tens of millions.

## Decision

Embeddings are stored in PostgreSQL using the `pgvector` extension, in ordinary
prefixed tables such as `ctx_embeddings`. There is no separate vector service.

The extension is enabled by a `packages/core` migration, because
`CREATE EXTENSION` is database-level; module tables chain onto it. The compose
image is `pgvector/pgvector:pg16`.

## Alternatives considered

**Chroma**, the original plan. Easy to run and adequate to about a million
vectors. Rejected because it is a second store: deleting a meeting means
deleting from PostgreSQL *and* from Chroma, with no transaction spanning both,
so a crash between the two leaves an orphaned embedding — exactly the retention
violation the privacy rules forbid. Filtering means either duplicating metadata
into Chroma and keeping it in sync, or fetching candidates and filtering in
application code.

**Qdrant.** Faster and more capable than either. Rejected as overkill at this
scale, with the same two-store problem.

**Weaviate.** Ships hybrid BM25 + vector search, which sounds like it removes
work. Rejected: its BM25 has no Korean analyzer, so we would supply Korean
lexical retrieval ourselves regardless, which cancels the advantage — and it is
the heaviest of the four to operate.

## Consequences

**Easier.** One store instead of two. Deletion stops being a special case:
embeddings cascade from `meetings.id` like every other row, so module D needs no
deletion hook for them and the class of bug where an embedding outlives its
meeting cannot occur. A similarity search and a metadata filter are one query,
in one transaction. Integration tests already run against real PostgreSQL, so
embeddings need no separate fixture or cleanup.

**Harder.** The `vector` column's dimension is fixed in DDL, so the embedding
model must be chosen before the table is created, and changing it later means a
migration that rewrites the column. Index tuning (HNSW parameters) becomes our
problem rather than a managed service's. The deployment target must offer
pgvector — RDS does from 15.2, Railway does.

**Accepted cost.** Hybrid retrieval is still two steps, not one query: PostgreSQL
full-text search has no Korean analyzer without a further extension, so BM25
stays in application code. The gain is on the vector-plus-filter half, not on
lexical retrieval.

**Revisit if** vector count approaches tens of millions, or a use case appears
that needs vector search decoupled from the relational database.
