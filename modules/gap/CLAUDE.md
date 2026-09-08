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

Entity and relation extraction → topic graph (rows + NetworkX) → participation matrix →
domain-template comparison → risk-scored gaps with generated questions.

## Consumes

`TranscriptReady` on `autune.transcript.ready`. Read-only access to
`meetings`, `participants`, `utterances`.

## Publishes

`GapReport` on `autune.gap.completed`, consumed by E.

## Owns

PostgreSQL only: `gap_topics`, `gap_topic_edges`, `gap_gaps`,
`gap_participation`, `gap_templates`.

Everything cascades from `meetings.id`, so no deletion hook is needed.

## AI stack

spaCy NER (Korean), rule-based relation extraction with LLM assistance,
NetworkX for PageRank and betweenness, weighted risk scoring.

The graph is one meeting's worth — tens of nodes — so it is built in memory from
rows each run. At that size PageRank and betweenness take single-digit
milliseconds; a graph database would buy nothing.
See `/docs/decisions/0005-no-graph-database.md`.

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
