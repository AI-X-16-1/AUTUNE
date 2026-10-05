# modules/extraction — Module B: Structured Extraction

Read `/CLAUDE.md` first. This file holds only what is specific to this module.
Full detail: `/docs/modules/extraction.md`.

| | |
| --- | --- |
| **Package** | `autune_extraction` |
| **Owner** | 강민구 |
| **Frontend** | `apps/web/src/features/actions/` |
| **Table prefix** | `ext_` — mandatory on every table this module creates |
| **API prefix** | `/api/extraction` |
| **Alembic branch** | `extraction` |

## What this module does

Classify utterances five ways → build action-item cards → verify ambiguous
agreement with NLI → sync to Notion, and put a confirmed item's due date on its
assignee's own Google Calendar, reading back a date they move there (#435), and
make each confirmed item one issue in the team's Jira project (#82, #458).
Jira is connected with one click over OAuth 3LO; its grant is person-bound, so
a revoked or expired grant asks the team to reconnect rather than failing
silently.

## Consumes

`TranscriptReady` on `autune.transcript.ready`. Read-only access to
`meetings`, `participants`, `utterances`.

## Publishes

`ExtractionResult` on `autune.extraction.completed`, consumed by **D and E**.

D depends on `decisions`: entities with a `dec_` id, the statement as settled,
and the utterances they came from. One decision often spans several utterances,
so a `Classification` with `kind="decision"` is not enough — D has nothing to
key a lineage on without the entity. Changing or dropping that field breaks D.

See `/docs/architecture/contracts.md`, "The B → D boundary".

`TeamActionProgress` on `autune.extraction.action_progress`, every ten
minutes for each team with a meeting in the last 91 days that is still inside
its retention window, consumed by **E** for its real completion rate (#605). A
meeting past `expires_at` is not counted. Counts per meeting and meeting ids only —
no assignee, title or item id, so no per-person completion record can be
built from it.

`TeamAgenda` on `autune.extraction.agenda_changed`, every five minutes, for
**D**'s pre-meeting brief (#436).

## Owns

`ext_classifications`, `ext_action_items`, `ext_action_item_sources`,
`ext_action_item_related`, `ext_decision_related`,
`ext_edit_events`, `ext_decisions`, `ext_decision_sources`, `ext_external_refs`,
`ext_confirmations`, `ext_decision_reviews`, `ext_decision_refs`,
`ext_calendar_events`, `ext_calendar_polls`, `ext_calendar_cleanup`, `ext_external_cleanup`,
`ext_due_reminders`, `ext_notion_targets`, `ext_sync_failures`, `ext_sync_retries`,
`ext_extraction_runs`, `ext_meeting_notes`.

The list in `/docs/modules/extraction.md` is the same set; keep the two together.
This one drifted once already — the B/D boundary commit updated "Publishes" here
and left "Owns" at the four tables the module had before any of them existed.

## AI stack

DeBERTa classifier (`kakaobank/kf-deberta-base`, #112; five kinds plus `none`),
NLI verification, LLM for reference resolution and report generation. Target
non-LLM share ~60% — classification and verification are trained models, not
prompts.

The five kinds: `commitment`, `decision`, `open_question`, `concern`,
`ambiguous`. The classifier has a sixth answer, `none` — most of a meeting is
none of the kinds (#149). `none` lives only inside this module
(`autune_extraction.labels`); the contract has no such kind, and an utterance
the model calls none is simply not in `ExtractionResult.classifications`.

## Privacy

- Send Notion and Jira only what an issue needs — description, assignee, due
  date. Never a transcript. A person's own calendar gets less: the description
  as the event title, the date, and no attendees (nobody is invited). The
  read-back asks Google for Autune's tagged events only, never the rest of the
  calendar.
- Send the LLM the smallest window that resolves a reference, and only masked
  text.
- Ambiguous-agreement confirmations are DMs to the speaker, never channel posts.

## Do not do here

- Detect what was missing (C) or link to past meetings (D).
- Query another module's tables. If you need C's topics, they belong in a
  contract.
- Write to `utterances` or any shared entity. Store your own state in `ext_*`.

## Metric

The classifier's macro F1 over the five kinds (ADR 0006), taken on an evaluation
set with `none` at its real proportion: `none` is scored — a none utterance
called `decision` costs `decision` its precision — but never averaged in.
Action item F1 is derived from it and reported beside the best published figure
for the task, 43.12 on AMI.

```bash
uv run --package autune-extraction python -m autune_extraction.eval \
    --eval-set dataset/extraction_eval.jsonl \
    --predictions runs/<model>.jsonl
```

The evaluation set is drawn from real meetings and is never committed.
