# Module B. Structured Extraction

| | |
| --- | --- |
| **Package** | `autune_extraction` |
| **Owner** | 강민구 |
| **Backend** | `modules/extraction/` |
| **Frontend** | `apps/web/src/features/actions/` |
| **Table prefix** | `ext_` |
| **API prefix** | `/api/extraction` |

## Responsibility

Turn utterances into trackable structure: classify what kind of statement each
utterance is, build action-item cards from commitments, verify ambiguous
agreement, and sync the result to Notion and Jira.

## Non-goals

- Finding what was *not* discussed — that is C. B works from what was said.
- Linking to past meetings — that is D.
- Team-level aggregation and scoring — that is E.

## Inputs

| Source | Contract |
| --- | --- |
| A | `TranscriptReady` via `autune.transcript.ready` |
| `packages/core` | `meetings`, `participants`, `utterances` (read-only) |

## Outputs

| Destination | Contract | Event |
| --- | --- | --- |
| D, E | `ExtractionResult` | `autune.extraction.completed` |
| Notion, Jira | Issue creation via `packages/integrations` | — |
| Slack | Action-item card thread, confirmation DMs | — |

## Pipeline

1. **Classify** — DeBERTa fine-tuned five-way classifier over each utterance:
   `commitment`, `decision`, `open_question`, `concern`, `ambiguous`.
2. **Resolve references** — LLM resolves pronouns and elided subjects ("그거",
   "저희가") against surrounding utterances.
3. **Slot fill** — extract assignee, task description, and due date from each
   commitment. Assignee maps to a `user_id` when possible.
4. **NLI verification** — check whether an apparent agreement entails an actual
   commitment. Weak assent ("한번 볼게요") is labeled `ambiguous`.
5. **Build decision entities** — group the utterances classified as decisions
   into `Decision` records with a `dec_` id and the statement as settled. One
   decision often spans several utterances. **Module D depends on this**: it is
   what a decision lineage is keyed on, and a `Classification` alone is not
   enough. See `../architecture/contracts.md`, "The B → D boundary".
6. **Confirm** — send a Slack DM to the speaker for each ambiguous agreement.
7. **Sync** — create Notion pages and Jira issues, storing the returned URLs.
8. **Publish** — emit `ExtractionResult`.

## Tables

| Table | Purpose |
| --- | --- |
| `ext_classifications` | Per-utterance kind, confidence, NLI result |
| `ext_action_items` | Assignee, description, due date, status, source utterances |
| `ext_external_refs` | Notion and Jira URLs per action item |
| `ext_confirmations` | Ambiguous-agreement DMs sent and their responses |
| `ext_decisions` | Decision entities, their statements and source utterances |

`ext_action_items` references `utterances.id`. It does **not** reference any
other module's tables.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/results/{meeting_id}` | Classifications and action items |
| GET | `/action-items` | Filter by assignee, status, due date |
| PATCH | `/action-items/{id}` | Edit or close an item |
| POST | `/results/{meeting_id}/sync` | Re-sync to Notion and Jira |

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |
| `autune.extraction.on_transcript_ready` | `autune.transcript.ready` | `cpu_heavy` |
| `autune.extraction.sync_external` | After extraction, or manual | `default` |
| `autune.extraction.send_confirmations` | After extraction | `default` |

## Slack surface

- Action-item card thread posted to the meeting channel
- A DM to each speaker with an ambiguous agreement, asking for confirmation
- Role-specific reports (Phase 2)

## AI stack

| Component | Model |
| --- | --- |
| Utterance classification | DeBERTa, fine-tuned |
| Agreement verification | NLI model |
| Reference resolution, report generation | LLM |
| Due-date parsing | Rule-based Korean date parser plus LLM fallback |

Target non-LLM share is roughly 60%: classification and verification are models
we train, not prompts.

### Classifier training data

Labels are produced by an LLM in a first pass over Korean meeting utterances and
then corrected by hand. Hand-labelling from nothing spends the only days this
project has for it, and labelling functions break down on exactly the two classes
that matter most here — `concern` and `ambiguous` are what feeds the NLI
confirmation step, and neither reduces to a keyword rule.

This does not spend the non-LLM budget above. That target describes what runs at
inference: it is "a design target, not a metric we measure... to keep the team
building real models rather than prompt chains" (`../product/prd.md` section 8).
An LLM that writes training labels produces a trained classifier, which is the
thing the target is asking for.

The label definitions come from the AMI Meeting Corpus rather than being invented
here, because AMI annotates the same boundaries already:

| Kind | AMI source |
| --- | --- |
| `commitment` | Dialogue act `Offer`; abstractive `actions` |
| `decision` | Extractive `decision` spans; abstractive `decisions` |
| `open_question` | The four `Elicit-*` dialogue acts |
| `concern` | Adjacency-pair `NEG`; abstractive `problems` |
| `ambiguous` | Adjacency-pair `UNC` and `PART` |

Two of those are worth knowing. `Suggest` is not a commitment — it is a proposal,
and it outnumbers `Offer` six to one, so folding it in buys noise. Polarity is not
in the dialogue-act inventory at all; `Assess` is the largest task act and carries
no sign, which is why `concern` is keyed on the adjacency pairs instead.

Corpora are downloaded per machine and never committed (`dataset/` is gitignored).
AMI is CC BY 4.0 and requires attribution wherever results are published. Analysis
scripts live in `modules/extraction/scripts/`.

Neither corpus is a Korean team meeting — AMI is English design roleplay, and the
Korean set is broadcast discussion. A model tuned on them has not been shown to
reach the F1 target on real meetings; an evaluation set drawn from the team's own
meetings is what would measure that gap.

## Metric

Action item extraction F1 — 0.80+ at six weeks, 0.88+ at three months.

```bash
uv run --package autune-extraction python -m autune_extraction.eval
```

## Privacy notes

- Only what an issue needs goes to Notion or Jira: the action description,
  assignee, and due date. Never the full transcript.
- The LLM used for reference resolution receives masked text only, and the
  smallest window that resolves the reference.
- Confirmation DMs go to the speaker, never to a channel.

## Open questions

- Whether Jira sync is per-action or batched per meeting.
- Whether reference resolution runs before classification or after. The pipeline
  above puts classification first; the argument for the other order is that
  assignees and objects parse more accurately once pronouns are resolved.
