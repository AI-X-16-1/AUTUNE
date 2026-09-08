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
agreement with NLI → sync to Notion and Jira.

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

## Owns

`ext_classifications`, `ext_action_items`, `ext_external_refs`,
`ext_confirmations`.

## AI stack

DeBERTa five-way classifier, NLI verification, LLM for reference resolution and
report generation. Target non-LLM share ~60% — classification and verification
are trained models, not prompts.

The five kinds: `commitment`, `decision`, `open_question`, `concern`,
`ambiguous`.

## Privacy

- Send Notion and Jira only what an issue needs — description, assignee, due
  date. Never a transcript.
- Send the LLM the smallest window that resolves a reference, and only masked
  text.
- Ambiguous-agreement confirmations are DMs to the speaker, never channel posts.

## Do not do here

- Detect what was missing (C) or link to past meetings (D).
- Query another module's tables. If you need C's topics, they belong in a
  contract.
- Write to `utterances` or any shared entity. Store your own state in `ext_*`.

## Metric

Action item extraction F1 — 0.80+ at six weeks.

```bash
uv run --package autune-extraction python -m autune_extraction.eval
```
