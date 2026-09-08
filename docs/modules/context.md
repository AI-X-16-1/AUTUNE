# Module D. Meeting Context Engine

| | |
| --- | --- |
| **Package** | `autune_context` |
| **Owner** | 문민재 |
| **Backend** | `modules/context/` |
| **Frontend** | `apps/web/src/features/context/` |
| **Table prefix** | `ctx_` |
| **API prefix** | `/api/context` |

## Responsibility

Keep context alive across meetings. Link the current meeting's topics to past
meetings, track how decisions changed over time, and — in Phase 2 — analyze
uploaded material into an agenda and send pre-meeting briefs.

D is what makes Autune different from every transcription tool. Everything else
processes "this meeting"; D connects meetings to one another.

## Non-goals

- Extracting decisions from a single meeting — B classifies them; D tracks how
  they change.
- Detecting what was missing — that is C.
- Team-level analytics — that is E.

## Inputs

| Source | Contract or form |
| --- | --- |
| A | `TranscriptReady` via `autune.transcript.ready` — topic linking |
| B | `ExtractionResult` via `autune.extraction.completed` — decision lineage |
| `packages/core` | `meetings`, `participants`, `utterances` (read-only) |
| Web upload | Material documents — PDF, docx, markdown (Phase 2) |
| Own history | `ctx_decisions`, `ctx_decision_versions`, `ctx_embeddings` |

## Outputs

| Destination | Contract | Event |
| --- | --- | --- |
| E | `ContextLinks` | `autune.context.completed` |
| Slack | Topic-link notifications, pre-meeting briefs (Phase 2) | — |

## Pipeline

### Topic linking
1. Extract topic statements from the transcript and embed them with
   Sentence-BERT.
2. Hybrid retrieval over past meetings: BM25 for lexical overlap, dense
   similarity for paraphrase. Retrieve the top 50.
3. Cross-encoder re-ranking over those 50, keep the top 10.
4. Above the confidence threshold, create a link; below it, offer the link for
   user confirmation rather than asserting it.

Retrieve broad, re-rank narrow. Topic mis-linking is the module's main risk, and
re-ranking is what buys precision.

### Decision lineage

Input is `ExtractionResult.decisions` — B decides what counts as a decision in
this meeting, D decides whether it is the same decision as one from before.
**Do not extract decisions here.** Duplicating B's classifier makes the two
disagree, and a decision then appears in the summary tab (S15) while missing
from the lineage view (S22), which reads to a user as a bug.

1. Match each of B's decisions to an existing thread, or open a new one. The
   thread id (`thr_`) is D's; the decision id (`dec_`) is B's.
2. Run NLI between the previous statement and the current one:
   `entailment` → unchanged, `contradiction` → reversed, `neutral` → modified.
3. Record a new version with what changed, when, in which meeting, and who was
   present.
4. Flag a change made while a key stakeholder was absent.

### Material analysis (Phase 2)
Chunk uploaded documents, embed into `ctx_embeddings`, retrieve against the meeting title
and participants, draft an agenda.

## Storage

| Store | Contents |
| --- | --- |
| PostgreSQL `ctx_materials` | Uploaded documents and chunk metadata |
| PostgreSQL `ctx_topic_links` | Meeting-to-meeting topic links with scores |
| PostgreSQL `ctx_decisions` | Decision threads |
| PostgreSQL `ctx_decision_versions` | Each version, with change type and NLI label |
| PostgreSQL `ctx_embeddings` | Embeddings for materials and meeting topics, in a `vector` column |
| Neo4j `CtxDecision` | Decision lineage graph for visualization |

Embeddings are PostgreSQL rows, so they cascade with the meeting like every
other table — no hook needed. Neo4j nodes still need D's own deletion hook,
because the cascade does not reach them.

The `vector` column's dimension is fixed at migration time, so the embedding
model has to be chosen before the table is created. The `vector` extension is
already enabled by a `packages/core` migration; chain your table onto that.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/links/{meeting_id}` | Topic links for a meeting |
| POST | `/links/{id}/confirm` | User confirms or rejects a link |
| GET | `/decisions/{decision_id}` | Full lineage timeline |
| GET | `/decisions` | Filter by team, topic, change type |
| POST | `/materials` | Upload material (Phase 2) |
| GET | `/briefs/{meeting_id}` | Pre-meeting brief (Phase 2) |

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |
| `autune.context.on_transcript_ready` | `autune.transcript.ready` | `cpu_heavy` |
| `autune.context.on_extraction_completed` | `autune.extraction.completed` | `cpu_heavy` |
| `autune.context.publish_if_ready` | after either half finishes, or on timeout | `default` |
| `autune.context.index_material` | Material upload | `cpu_heavy` |
| `autune.context.send_brief` | 30 minutes before a meeting (Phase 2) | `default` |

## Slack surface

- Topic-link notification: "이 안건은 2026년 9월 4일 회의에서 논의된 적 있습니다"
  with a link to the minutes
- Decision-drift warning when a decision changed while a key stakeholder was
  absent
- Pre-meeting brief 30 minutes before the meeting (Phase 2)

## AI stack

| Component | Model |
| --- | --- |
| Dense retrieval | Sentence-BERT (Korean) |
| Lexical retrieval | BM25, in application code |
| Re-ranking | Cross-encoder |
| Decision-change detection | NLI |
| Agenda and brief generation | LLM (Phase 2) |
| Vector store | pgvector, in PostgreSQL |

## Metric

Topic linking accuracy — 0.75+ at six weeks, 0.85+ at three months.

```bash
uv run --package autune-context python -m autune_context.eval
```

## Privacy notes

- Retention interacts directly with this module: a linked past meeting may be
  deleted by the retention sweep. Handle a dangling link gracefully — show that
  the meeting is gone, never resurrect its content from an embedding.
- Embeddings are derived from masked text. Deleting a meeting deletes its
  embeddings automatically, because they are rows in a table that cascades from
  `meetings.id` — an embedding outliving its meeting would be a retention
  violation, and pgvector removes the way that used to happen.
- Briefs sent to Slack contain summaries, never transcript excerpts beyond what
  the brief needs.

## Open questions

- Confidence threshold for asserting a link versus asking the user.
