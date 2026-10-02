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
changed across meetings. Shortly before a scheduled meeting, post a brief
recapping the meeting it follows (`briefs.py`). Material analysis and agenda
generation are Phase 2.

## Agent tools

`tools.py` exposes three reads to the agent layer (`links_for_meeting`,
`decision_thread`, `list_decisions`). They never return
`key_stakeholders_absent`, and a quoted `previous_statement` only while its
predecessor meeting is visible; `tests/integration/test_tools.py` pins both.
A new tool must go through a `service` read, never a query of its own.

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

The pre-meeting brief goes to Slack only, on a clock
(`autune.context.periodic.send_due_briefs`). Its agenda is Jira issues, which
are B's integration: D never calls Jira. B publishes a `TeamAgenda` every five
minutes, `on_extraction_agenda_changed` keeps the latest per team, and a
snapshot older than `AGENDA_STALE_AFTER` counts as no agenda.

## Owns

PostgreSQL only: `ctx_topic_links`, `ctx_decisions`, `ctx_decision_versions`,
`ctx_embeddings` (a `vector` column, via pgvector), `ctx_meeting_status`,
`ctx_briefs` (which past meeting a brief recaps — never the recap itself),
`ctx_team_agendas` (B's latest open-issue snapshot per team; the one table that
copies text from another module, so it is purged once stale).
`ctx_materials` is Phase 2.

Four of those cascade from `meetings.id`. `ctx_decisions` is anchored on
`team_id` instead — a lineage must outlive its origin meeting reaching the
retention window — so a thread left with no versions is dead weight.
`service.sweep_orphan_decision_threads` removes them; it is not yet an
`autune_core.deletion` hook (blocked on #87, same as module E's `intel_reports`).
See `/docs/modules/context.md`, "Deletion".

A lineage is a chain: `previous_version_id` plus a recursive CTE. The graph
visualisation on S22 is Phase 2 and belongs to the frontend.

The `vector` dimension is fixed when you create the table, so pick the embedding
model first. The extension is enabled by a `packages/core` migration; the
`ctx_embeddings` revision `depends_on` it so `upgrade heads` orders them.

## AI stack

Sentence-BERT + BM25 hybrid retrieval, cross-encoder re-ranking, NLI for
decision-change detection, LLM for agenda generation (Phase 2). The
pre-meeting brief is a template over this module's own rows; no LLM.

`AUTUNE_CONTEXT_ENGINE_MODE=llm` swaps the three linking/lineage judgements (same
topic, same decision thread, how it changed) for an external LLM; `hybrid` keeps
the trained stack and lets the LLM veto topic links it is about to assert. Compare
them with the trained stack: `python -m autune_context.eval --mode all`. Default is
`classic`; the provider (`openai` | `gemini` | `anthropic`) is chosen by
`AUTUNE_CONTEXT_LLM_IMPL`, with no default. The LLM clients sit on `autune_integrations.HttpClient` so
`check_outbound` runs on every call — never call an LLM API any other way. See
`/docs/modules/context.md`, "Engine mode".

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
- Build Phase 2 features during the six weeks — material analysis and agenda
  generation come after the MVP. (Briefs moved into the build on 2026-09-29.)

## Metric

Topic linking accuracy — 0.75+ at six weeks.

```bash
uv run --package autune-context python -m autune_context.eval
```
