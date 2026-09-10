# Module C. Gap Detection

| | |
| --- | --- |
| **Package** | `autune_gap` |
| **Owner** | 박재경 |
| **Backend** | `modules/gap/` |
| **Frontend** | `apps/web/src/features/gap/` |
| **Table prefix** | `gap_` |
| **API prefix** | `/api/gap` |

## Responsibility

Find what the meeting should have covered and did not. Build a topic graph from
the transcript, measure who participated in which topic, compare against a
domain template, and score the risk of each missing item.

## Non-goals

- Extracting what *was* said into action items — that is B.
- Connecting to past meetings — that is D. C works within one meeting.
- Predicting future misalignment — that is E.

## Inputs

| Source | Contract |
| --- | --- |
| A | `TranscriptReady` via `autune.transcript.ready` |
| `packages/core` | `meetings`, `participants`, `utterances` (read-only) |

## Outputs

| Destination | Contract | Event |
| --- | --- | --- |
| E | `GapReport` | `autune.gap.completed` |
| Slack | Gap report and question cards | — |

## Pipeline

1. **NER** — spaCy extracts entities: features, systems, metrics, people, dates.
2. **Relation extraction** — build subject–relation–object triples, with LLM
   assistance for hard cases.
3. **Topic graph** — persist nodes and edges as rows (`gap_topics`,
   `gap_topic_edges`), then load them into NetworkX.
4. **Centrality** — PageRank and betweenness identify which topics carried the
   meeting.
5. **Participation matrix** — per topic, who spoke and who was silent.
6. **Template comparison** — match the meeting against a domain template and
   find unfilled items.
7. **Risk scoring** — score each gap using topic centrality, participation
   imbalance (a topic no engineer spoke on is riskier), and template weight.
8. **Question generation** — produce a concrete question that would close each
   gap.
9. **Publish** — emit `GapReport`.

## Storage

| Store | Contents |
| --- | --- |
| PostgreSQL `gap_topics` | Topic nodes with PageRank and betweenness, per meeting |
| PostgreSQL `gap_topic_utterances` | Which utterances a topic was built from, in order |
| PostgreSQL `gap_topic_edges` | Relations between topics, directed, per meeting |
| PostgreSQL `gap_participation` | Topic × participant speech presence |
| PostgreSQL `gap_gaps` | Detected gaps, category, severity, risk score, question |
| PostgreSQL `gap_related_topics` | Which topics a gap was inferred from |
| PostgreSQL `gap_templates` | Domain templates and their items — **not built yet**, see below |

Everything that exists cascades from `meetings.id`, so no deletion hook is
needed.

`gap_topic_utterances` and `gap_related_topics` are link tables rather than
JSONB lists on their parents. The report joins both back — to `utterances` for
the quotation behind a topic, to `gap_topics` for why a gap was raised — and
`../architecture/data-model.md` rules JSONB out for anything you join on. They
store ids and never the text: a copy of an utterance here would leave transcript
content behind a cascade that no longer reaches it.

`gap_topics` keeps PageRank and betweenness in separate columns rather than one
blended score. Risk scoring weights them differently, and a single number could
not be re-weighted afterwards without rebuilding the graph.

`gap_gaps.dismissed_at` marks a false positive without hiding the row —
threshold tuning has to read what was dismissed, and soft deletes are forbidden
(`../architecture/data-model.md`). No dismisser is recorded: which teammate
pressed the button is not something tuning needs, and storing it would be a
per-person record of conduct that ADR 0003 refuses.

### `gap_templates` is deferred, not forgotten

A domain template is reference data — it is not derived from any meeting, so it
is the one table this module owns that cannot cascade from `meetings.id`. What
it *does* hang off, a team or nothing at all, follows from who writes templates
and how many there are, which is open as issue #22. Creating it now means
guessing an anchor and migrating away from it later.

It blocks nothing in the meantime: template comparison (#14) is waiting on the
same decision.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/reports/{meeting_id}` | Full gap report |
| GET | `/topics/{meeting_id}` | Topic graph for visualization |
| POST | `/gaps/{id}/dismiss` | Mark a gap as a false positive (feeds threshold tuning) |
| GET | `/templates` | Available domain templates |

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |
| `autune.gap.on_transcript_ready` | `autune.transcript.ready` | `cpu_heavy` |

## Slack surface

- Gap report thread in the meeting channel, `high` severity only by default
- Generated question cards teams can act on

## AI stack

| Component | Model or algorithm |
| --- | --- |
| Entity extraction | spaCy NER (Korean model) |
| Relation extraction | Rule-based patterns plus LLM assistance |
| Graph | NetworkX, in memory |
| Topic importance | PageRank, betweenness centrality |
| Risk scoring | Weighted heuristic; thresholds in `config.py` |

### Model abstraction layer

Every model sits behind a Protocol in `autune_gap.pipeline.base`, and the
implementation is chosen by `AUTUNE_GAP_*_IMPL` and reached through
`registry`. Nothing outside that package names a model class, so swapping a
model does not touch a caller. Modules B and D settled on the same shape.

`FakeNer` is what the tests run. It is deterministic, needs no weights and no
network, and it exists so the topic graph, the centrality pass and the
participation matrix can be built and tested before the real extractor lands
(#13). It is **not** an approximation of the model's accuracy and must not be
used to estimate it.

There is no `external` implementation and adding one is a privacy decision
rather than a config string — see `../engineering/environments.md`, "The entity
extractor has no external option".

Every row a topic produces records `extractor_version` — the pipeline name and
its version, `ko_core_news_lg-3.8.0`. The name alone is not a version: the
pipeline ships a new release with every spaCy minor, so a graph built with 3.7
and one built with 3.8 would carry the same string. Gap precision is measured
over time and dismissals feed threshold tuning; both read across model
versions, so a row that cannot name its extractor takes part in neither. The
version comes from the pipeline's own `meta`, and the wheel is pinned in the
`local-models` extra so `uv.lock` decides it rather than the day somebody ran
`spacy download`.

### `ko_core_news_lg` is CC BY-SA 4.0

The pipeline and both of its annotated sources — UD Korean Kaist v2.8 and
KLUE v1.1.0 — are CC BY-SA 4.0. (Its vectors are CC0.)

Running it inside our own infrastructure is unencumbered. **ShareAlike bites if
a model derived from it is distributed**, which is the shape #13 takes: a
pipeline fine-tuned from these weights, published or shipped to a customer,
carries the same licence onward. Read the meta before assuming otherwise:

```bash
uv run --package autune-gap --extra local-models python -c \
  "import spacy; print(spacy.load('ko_core_news_lg').meta['license'])"
```

The same question module B has open for its classifier checkpoint and AMI
(#112). If #13 trains from a differently licensed base instead, this note is
what says why that mattered.

Entities are normalised onto five labels — `feature`, `system`, `metric`,
`person`, `date` — rather than spaCy's own inventory. A model trained on news
text emits `ORG` and `LOC`, and a meeting about search ranking has no
organisations in it worth graphing. `feature` and `system` have no spaCy
equivalent at all: they are this product's vocabulary, so a general model
cannot supply them and #13 is where they come from. Until then the graph is
built from the three a general model does give, and the mapping in
`pipeline.ner` shows that limit rather than hiding it behind an empty class.

There is deliberately **no `worker_process_init` warm-up hook**. `apps/worker`
imports every module's `tasks.py` into one Celery app, so a hook registered
here would run in every worker process regardless of `-Q` — including workers
that never run a gap task. Module D shipped one and had to gate it behind a
setting (PR #90). The cost of not having one is that the first task of a
process pays the model load.

## Metric

Gap detection precision — 0.70+ at six weeks, 0.82+ at three months.

Precision, not recall: a false gap costs user trust, a missed gap costs
nothing they did not already have. Only `high` severity is shown by default,
and dismissals feed threshold tuning.

```bash
uv run --package autune-gap python -m autune_gap.eval
```

## Privacy notes

- The participation matrix records **whether** a participant spoke on a topic,
  not how much. It is topic coverage, not speech volume — do not let it drift
  into a per-person talk-time metric. See `../architecture/privacy.md` section 3.
  `gap_participation.spoke` is a boolean and a test asserts the whole column set
  so it stays one.
- **The boolean is not the whole guarantee — how the report reads it matters.**
  Summing the matrix *along a person* ("spoke on 1 of 12 topics") rebuilds the
  speaking-ratio metric the column shape was chosen to prevent, out of data that
  is individually harmless. The report reads it along a *topic* instead
  ("검색 랭킹 — 백엔드 쪽 발언 없음"), which is what a gap is and what the whole
  team may see. Raised in review of #134; the surface it constrains is #36.
- Topic labels derived from transcript text are already masked upstream. Do not
  re-derive anything from an unmasked source; there is not one.

## The topic graph is per meeting

Decided 2026-09-08 (issue #23): C builds a fresh subgraph for each meeting and
does not accumulate across meetings.

Nothing C produces needs accumulation. Centrality answers "which topics carried
*this* meeting", the participation matrix and template comparison are
within-meeting by definition, and risk scoring feeds on all three. C's metric is
gap detection precision, which accumulation does not help.

What it avoids:

- **Two mechanisms answering the same question.** D already matches topics
  across meetings with SBERT, BM25 and cross-encoder re-ranking. A second,
  graph-based matcher would disagree with it, and users would see the
  contradiction.
- **Ambiguous centrality.** A topic central to today's meeting and a topic
  central to the quarter are different things; an accumulated PageRank measures
  the second while the gap report needs the first.
- **Surgical deletion.** Retention has to remove one meeting's data
  (`../architecture/privacy.md` section 4). Dropping a per-meeting subgraph is
  trivial; unpicking one meeting's contribution from a shared graph, where edges
  may be shared, is error-prone.

The dashboard's topic-recurrence figure (S26) does not need it either: D's
`topic_links` already say a topic came up before, so E counts those.

## Open questions

- How many domain templates for the MVP, and who authors them.
