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

`TranscriptReady` on `autune.transcript.ready`, read-only shared entities,
uploaded material (Phase 2), and this module's own history.

## Publishes

`ContextLinks` on `autune.context.completed`, consumed by E.

## Owns

- PostgreSQL: `ctx_materials`, `ctx_topic_links`, `ctx_decisions`,
  `ctx_decision_versions`
- Chroma: `ctx_materials`, `ctx_meeting_topics`
- Neo4j: `CtxDecision` lineage

Chroma embeddings and Neo4j nodes need this module's own deletion hook — the
Postgres cascade reaches neither.

## AI stack

Sentence-BERT + BM25 hybrid retrieval, cross-encoder re-ranking, NLI for
decision-change detection, LLM for agenda and brief generation (Phase 2).

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
