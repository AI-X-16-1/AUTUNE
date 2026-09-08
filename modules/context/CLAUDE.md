# modules/context — Module D: Meeting Context Engine

Read `/CLAUDE.md` first. This file holds only what is specific to this module.
Full detail: `/docs/modules/context.md`.

| | |
| --- | --- |
| **Package** | `autune_context` |
| **Owner** | 문민재 |
| **Frontend** | `apps/web/src/features/context/` |
| **Table prefix** | `ctx_` — mandatory on every table this module creates |
| **API prefix** | `/api/context` |
| **Alembic branch** | `context` |

## What this module does

Link the current meeting's topics to past meetings, and track how decisions
changed across meetings. Material analysis, agenda generation, and pre-meeting
briefs are Phase 2.

## Consumes

Two inputs, and they arrive at different times:

- `TranscriptReady` on `autune.transcript.ready` → **topic linking**. Needs only
  the transcript, so it runs in parallel with B and C.
- `ExtractionResult` on `autune.extraction.completed` → **decision lineage**.
  Needs `result.decisions`, so it runs after B.

Plus read-only shared entities, uploaded material (Phase 2), and this module's
own history.

**Do not extract decisions here.** B owns what counts as a decision in a
meeting; D owns whether it is the same decision as one from before. Duplicating
B's classifier makes the two disagree, and a decision then shows in the summary
tab and vanishes from the lineage view. `thread_id` (`thr_`) is yours;
`source_decision_id` (`dec_`) is B's.

**Publish even when B fails.** A failure in B must not cost the user their topic
links: publish `ContextLinks` with an empty `decision_lineage` and
`"extraction"` in `missing_sources`.

## Publishes

`ContextLinks` on `autune.context.completed`, consumed by E.

## Owns

PostgreSQL only: `ctx_materials`, `ctx_topic_links`, `ctx_decisions`,
`ctx_decision_versions`, `ctx_embeddings` (a `vector` column, via pgvector).

Everything cascades with the meeting, so no deletion hook is needed.

A lineage is a chain: `previous_version_id` plus a recursive CTE. The graph
visualisation on S22 is Phase 2 and belongs to the frontend.

The `vector` dimension is fixed when you create the table, so pick the embedding
model first. The extension is enabled by a `packages/core` migration already.

## AI stack

Sentence-BERT + BM25 hybrid retrieval, cross-encoder re-ranking, NLI for
decision-change detection, LLM for agenda and brief generation (Phase 2).

Vector search runs in PostgreSQL through pgvector, so a similarity search and a
metadata filter (`team_id`, `meeting_id`, retention window) are one query. BM25
stays in application code: PostgreSQL full-text search has no Korean analyzer
without a further extension, so hybrid retrieval is not a single query.

**Retrieve broad, re-rank narrow.** Top 50 from hybrid retrieval, top 10 after
re-ranking. Mis-linking is this module's main risk, and re-ranking is what buys
precision. Below the confidence threshold, ask the user rather than asserting
the link.

## Privacy

- A linked past meeting can be deleted by the retention sweep. Handle a dangling
  link gracefully — show the meeting is gone, never reconstruct its content from
  an embedding.
- Deleting a meeting deletes its embeddings. An embedding that outlives its
  meeting is a retention violation.

## Do not do here

- Classify utterances (B) or detect gaps within one meeting (C).
- Compute team analytics (E).
- Build Phase 2 features during the six weeks — material analysis, agenda
  generation, and briefs come after the MVP.

## Metric

Topic linking accuracy — 0.75+ at six weeks.

```bash
uv run --package autune-context python -m autune_context.eval
```
