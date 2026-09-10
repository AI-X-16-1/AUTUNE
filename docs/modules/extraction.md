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

Classification runs before reference resolution, which is worth stating because
the opposite reads as more natural: resolve the pronouns, then work on clean
text. Two things decide it.

The class is marked at the sentence ending in Korean, and the referent does not
carry it — "이걸 확정하도록 **하겠습니다**" is a commitment whether or not anything
knows what 이걸 points at. Slot filling is the step that genuinely cannot proceed
unresolved, and it comes after resolution either way.

Resolution is an LLM call. Running it first means one per utterance; running it
after classification means one per utterance in the classes that still need it —
roughly an eleventh as many on a corpus of 398,748 meeting utterances. That also
points the same way as `privacy.md`, which asks for the smallest window that
resolves a reference rather than the whole meeting.

Neither argument is an accuracy measurement — comparing the two orders needs a
labelled set and two trained classifiers. If the evaluation harness later shows
resolution-first classifies better, moving the step is the cheap direction to go;
building on an LLM call per utterance and cutting it back later is not. Keep the
step positionable. `modules/extraction/scripts/ko_reference_overlap.py` measures
the overlap the question turns on.

## Tables

| Table | Purpose |
| --- | --- |
| `ext_classifications` | Per-utterance kind, confidence, NLI result |
| `ext_action_items` | Assignee, description, due date, status, origin |
| `ext_action_item_sources` | Which utterances an item came from |
| `ext_edit_events` | One row per correction. Counts only — no person on it |
| `ext_external_refs` | Notion and Jira URLs per action item |
| `ext_confirmations` | Ambiguous-agreement DMs sent and their responses |
| `ext_decisions` | Decision entities, their statements and source utterances |
| `ext_decision_sources` | Which utterances a decision was settled in, in order |

`ext_action_items.origin` is `model` or `user`. ADR 0006 makes the output a draft
the user completes, so an item somebody typed is an ordinary row rather than an
anomaly — and telling the two apart is what edit cost is measured against.

Source utterances are a table rather than a JSONB list because the detail drawer
joins them back to read the quotation, and `data-model.md` rules JSONB out for
anything you join on.

`ext_edit_events` carries no user id and must not gain one. ADR 0003 forbids
per-person metrics, and "who corrected the model most" is the same shape of data
as a speaking ratio. Its `action_item_id` clears on delete rather than cascading:
cascading would remove the evidence that the model was wrong along with the wrong
item, and the metric would improve every time somebody deleted something.

`ext_decisions` carries no owner column. ADR 0007 makes a record reachable by
`meeting_id` the meeting's, and a decision is the clearest case of it: the team
is still bound by what was settled after the person who proposed it leaves.

`ext_decision_sources` keeps a `position` so the sources come back in meeting
order without a second join. The order carries the argument — the proposal
first, the sentence that settles it last — and the statement is taken from the
last one.

Rebuilding a meeting's decisions replaces them, and the new rows get fresh `dec_`
ids. A caller that rebuilds must republish `ExtractionResult`, because D's
lineage points at the old ids otherwise. Matching an old decision to a new one is
the same-decision question, and #25 gave that to D.

`ext_action_items` references `utterances.id`. It does **not** reference any
other module's tables.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/results/{meeting_id}` | Classifications and action items |
| GET | `/action-items` | Filter by assignee, status, due date |
| PATCH | `/action-items/{id}` | Edit or close an item |
| POST | `/action-items` | Add an item the model missed |
| DELETE | `/action-items/{id}` | Delete an item the model got wrong |
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

## User correction

Everything the pipeline produces is a draft. ADR 0006 sets the rule: an item can
be edited, deleted, or added by hand, every item carries the utterances it came
from, and items below the confidence threshold appear as candidates rather than
being dropped. Recall is ranked above precision for that reason — a wrong item
costs a click, a missing one costs re-reading the meeting.

Corrections stay in the meeting. They update `ext_action_items` and increment the
edit-cost counters; they are never exported as training labels (ADR 0003), and
edit cost is aggregated per meeting, never per person.

A deleted item is deleted. `privacy.md` allows no soft deletes and no tombstones
holding content, and edit cost does not need one: the counter records that a
deletion happened, which is the whole of what the metric asks. Keeping the row to
remember the model was wrong would be keeping meeting content for a reason the
privacy rules do not grant.

## Metric

The classifier's five-way macro F1 is what we train against and what the harness
scores. Action item F1 is derived from it and reported beside the best published
figure for the task, per ADR 0006.

| Metric | Six weeks | Three months |
| --- | --- | --- |
| Action item F1 | 0.43 — matching the best published AMI result, 43.12 (ADR 0006) | above it |
| Classifier macro F1, five-way | set in week 2 from the AMI dialogue-act literature, once the evaluation set exists | above it |
| Items the user accepts with no edit | the first measurement is the baseline | improve on it |

```bash
uv run --package autune-extraction python -m autune_extraction.eval \
    --eval-set dataset/extraction_eval.jsonl \
    --predictions runs/<model>.jsonl
```

The evaluation set is drawn from real meetings and is never committed. The
harness scores a predictions file rather than loading a model, so a run can be
rescored without a GPU and the metric means the same thing across model
versions.

## Privacy notes

- Only what an issue needs goes to Notion or Jira: the action description,
  assignee, and due date. Never the full transcript.
- The LLM used for reference resolution receives masked text only, and the
  smallest window that resolves the reference.
- Confirmation DMs go to the speaker, never to a channel.

## Open questions

- Whether Jira sync is per-action or batched per meeting.
