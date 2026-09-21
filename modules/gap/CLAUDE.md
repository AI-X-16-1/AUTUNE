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

## Thresholds live in `config.py`

Every severity band, weight and damping factor is an `AUTUNE_GAP_*` setting.
Nothing that decides a gap's severity may be a literal in `detect.py` (#35), and
a new one goes in `docs/engineering/environments.md` and `.env.example` in the
same PR.

## Consumes

`TranscriptReady` on `autune.transcript.ready`. Read-only access to
`meetings`, `participants`, `utterances`.

## Publishes

`GapReport` on `autune.gap.completed`, consumed by E.

## Owns

PostgreSQL only: `gap_topics`, `gap_topic_utterances`, `gap_topic_edges`,
`gap_participation`, `gap_gaps`, `gap_related_topics`,
`gap_meeting_template`.

The list in `/docs/modules/gap.md` is the same set; keep the two together.

`gap_topic_utterances` and `gap_related_topics` are link tables the original
sketch folded into their parents. They are tables rather than JSONB lists
because the report joins them back — to `utterances` for the quotation, to
`gap_topics` for why a gap was raised — and `data-model.md` rules JSONB out for
anything you join on.

`gap_templates` is **not built, and does not need to be.** A domain template is
reference data, not something derived from a meeting, so it is the one table
here that could not cascade from `meetings.id` — and what it would hang off (a
team, or nothing at all) depends on who writes templates and how many there are,
which is issue #22. So the templates are files in the package instead —
`src/autune_gap/templates/*.yaml`, loaded by `template.py` — and the anchor
question does not arise. Git holds their history and an edit goes through PR
review, which is the right control for content that decides gap precision.
Build the table when a team writes its own template (Phase 2); a real user flow
names the anchor then.

**Bump a template file's `version` whenever an item changes.** It is recorded on
every gap that template raises, and precision measured across an edit is
otherwise two different checklists averaged together.

`gap_meeting_template` stores only the exception — one row for a meeting
somebody pointed at a non-default template. Everything here still cascades from
`meetings.id`, so no deletion hook is needed.

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
