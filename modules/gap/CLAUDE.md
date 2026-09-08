# modules/gap — Module C: Gap Detection

Read `/CLAUDE.md` first. This file holds only what is specific to this module.
Full detail: `/docs/modules/gap.md`.

| | |
| --- | --- |
| **Package** | `autune_gap` |
| **Owner** | 박재경 |
| **Frontend** | `apps/web/src/features/gap/` |
| **Table prefix** | `gap_` — mandatory on every table this module creates |
| **API prefix** | `/api/gap` |
| **Alembic branch** | `gap` |

## What this module does

Entity and relation extraction → topic graph in Neo4j → participation matrix →
domain-template comparison → risk-scored gaps with generated questions.

## Consumes

`TranscriptReady` on `autune.transcript.ready`. Read-only access to
`meetings`, `participants`, `utterances`.

## Publishes

`GapReport` on `autune.gap.completed`, consumed by E.

## Owns

- PostgreSQL: `gap_topics`, `gap_gaps`, `gap_participation`, `gap_templates`
- Neo4j: `GapTopic` nodes and their relations

Neo4j nodes are **not** removed by the Postgres cascade. Register a deletion
hook and test it.

## AI stack

spaCy NER (Korean), rule-based relation extraction with LLM assistance, Neo4j,
PageRank and betweenness centrality, weighted risk scoring.

## Privacy

The participation matrix records **whether** someone spoke on a topic, not how
much. It is topic coverage, not speech volume. Do not let it drift into a
per-person talk-time metric — see `/docs/architecture/privacy.md` section 3.

## Do not do here

- Extract action items (B) or link across meetings (D).
- Persist a cross-meeting topic graph. Cross-meeting linking is D's job; C works
  within one meeting.
- Surface `medium` or `low` severity gaps by default. Only `high` is shown.

## Metric

Gap detection precision — 0.70+ at six weeks. Precision, not recall: a false gap
costs user trust. Dismissals feed threshold tuning.

```bash
uv run --package autune-gap python -m autune_gap.eval
```
