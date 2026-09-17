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

1. **Classify** — a fine-tuned DeBERTa classifier over each utterance, in
   spoken order: `commitment`, `decision`, `open_question`, `concern`,
   `ambiguous`, or **`none`** — most of a meeting is none of them (#149).
   `none` never leaves this module: an utterance the model calls none is simply
   absent from `ExtractionResult.classifications`, and has no row in
   `ext_classifications`. Inference runs before any database transaction opens;
   it is minutes of CPU per meeting.
2. **Resolve references** — LLM resolves pronouns and elided subjects ("그거",
   "저희가") against surrounding utterances.
3. **Slot fill** — one draft action item per commitment. The assignee is the
   speaker: their `user_id` when identified, otherwise only the transcript's
   label. The due date is the first Korean date phrase in the utterance
   ("다음 주 화요일", "월말", "9/20"), resolved against the day the meeting was
   held in Korea, and the phrase itself is kept in `due_text`. With no meeting
   start time, a relative phrase keeps its words and gets no date — the upload
   time is not the meeting time. Anything unsettled is left empty and the item
   stays in *needs confirmation*.
4. **NLI verification** — check whether an apparent agreement entails an actual
   commitment. Weak assent ("한번 볼게요") is labeled `ambiguous`.
5. **Build decision entities** — group the utterances classified as decisions
   into `Decision` records with a `dec_` id and the statement as settled. One
   decision often spans several utterances. **Module D depends on this**: it is
   what a decision lineage is keyed on, and a `Classification` alone is not
   enough. See `../architecture/contracts.md`, "The B → D boundary".
6. **Confirm** — every ambiguous agreement is recorded in `ext_confirmations`
   first, then the speaker gets a Slack DM. Until the DM goes out the row is
   *not asked* and `AmbiguousAgreement.confirmation_sent` is false; sending
   needs the speaker's Slack account (#70) and a team Slack client (#30).
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
| `ext_classifications` | Per-utterance kind, confidence, model version, NLI result. Kinds only — no row for `none` |
| `ext_action_items` | Assignee, description, due date, status, origin |
| `ext_action_item_sources` | Which utterances an item came from |
| `ext_edit_events` | One row per correction. Counts only — no person on it |
| `ext_external_refs` | Notion and Jira URLs per action item |
| `ext_confirmations` | Every ambiguous agreement, the DM once sent, and the response |
| `ext_decisions` | Decision entities, their statements and source utterances |
| `ext_decision_sources` | Which utterances a decision was settled in, in order |
| `ext_decision_reviews` | A person's verdict on each proposed decision (pending, confirmed, rejected) and an optional rewording, keyed by `dec_` id so a rerun over the same sources keeps it (#246). No reviewer column |

A meeting that is processed again replaces its model-made rows —
classifications, decisions, and draft items — rather than adding a second set,
which is what makes a redelivered task safe. The one exception is the draft:
once a person has edited anything in the meeting, a rerun leaves its items
alone, because ADR 0006 makes the list theirs to finish.

`ext_action_items.due_text` is the phrase a model item's due date was read from,
for S18. It is cleared when a person sets the date themselves: the phrase no
longer explains the value (#109).

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

Rebuilding a meeting's decisions replaces them, but a decision's `dec_` id is
derived from the meeting and the utterances it was settled in, so a rebuild over
the same labels and the same utterance ids keeps the same ids (#171). A decision
whose sources changed gets a different id — and module A mints new `utt_` ids
whenever it reprocesses a recording (#194), which changes every source — so a
caller that rebuilds still republishes `ExtractionResult`. Matching an old decision to a reworded new one is the
same-decision question, and #25 gave that to D.

`ext_action_items` references `utterances.id`. It does **not** reference any
other module's tables.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/results/{meeting_id}` | The meeting's `ExtractionResult`, built from what is stored |
| GET | `/action-items` | Filter by `meeting_id`, `assignee_id`, `status`, `due_before` (strict). Source utterance ids, never their text |
| GET | `/action-items/{id}` | One item and the text of its source utterances, in spoken order |
| PATCH | `/action-items/{id}` | Edit or close an item |
| POST | `/action-items` | Add an item the model missed |
| DELETE | `/action-items/{id}` | Delete an item the model got wrong |
| POST | `/results/{meeting_id}/sync` | Re-sync to Notion and Jira |
| GET | `/reviews/{meeting_id}` | What needs a person before anything is sent: decisions with their verdict, weak assents with their DM state, items still `needs_confirmation` or below the candidate line (S15, #246) |
| PATCH | `/decisions/{id}` | Confirm, reject, reword, or put back to pending one proposed decision |
| GET | `/reviews/{meeting_id}/outbound` | Exactly what may leave for Notion, Slack or Jira: confirmed decisions and accepted items. The sync reads this and nothing else |

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

The table above lives in code as `autune_extraction.labeling.ami`, along with the
acts it deliberately leaves out and the reason for each. Three things read it —
the corpus loader, the LLM labelling prompt, and the hand-correction pass — and
three paraphrases of a table drift apart. All sixteen of AMI's leaf acts are
either mapped or excluded by name, because an act nobody decided about looks
exactly like an act somebody forgot.

An utterance can carry evidence from several layers at once: an `Offer` inside a
UNC adjacency pair is both a commitment and an ambiguity. `PRECEDENCE` settles
those, highest first:

```
decision  >  concern  >  open_question  >  ambiguous  >  commitment
```

AMI does not state an ordering, so this one was a judgement — and then
`scripts/ami_label_conflicts.py` measured it. Over 117,915 dialogue acts it
labels 17,876 and finds **809 contested (4.5%)**, so the ordering decides real
training data rather than a handful of edge cases.

| Rule | Cases |
| --- | --- |
| `open_question` over `ambiguous` | 406 |
| `decision` over anything | 365 |
| `concern` over the two below it | 33 |
| `ambiguous` over `commitment` | 5 |

The measurement changed the ordering. `ambiguous` originally outranked
`open_question`, on the reasoning that polarity is the better-informed layer.
The cases it produced are questions, not hedged assent — "Do we need an LCD
display?" — and an `ambiguous` label triggers a DM asking the speaker whether
they meant to commit. Asking that about a question is not a near miss.

The `ambiguous` over `commitment` rule is the "한번 볼게요" case and is right where
it fires, but it fires five times, and structurally so: polarity is a property of
a response while an `Offer` is an initiating move, so the two rarely land on one
utterance. Keep the rule; tune nothing on it.

**A decision span promotes only an act that asserts something.** The extractive
layer marks a *region* — the stretch a human selected as evidence — not one
utterance, and promoting everything inside it produced 9,835 `decision` labels
from AMI's 288 annotated decisions. A third of those were acts the table above
already excludes by name: 1,331 Fragments, 1,271 Backchannels, 730 Stalls, so
the corpus taught that "Hmm." and "Yeah." are where a meeting settles something.
`ASSERTIVE_ACTS` is the four that put something on the record — `Inform`,
`Assess`, `Suggest`, `Offer` — and membership is required in addition to the
region, never instead of it.

Each label records which layer produced it and which kinds it overruled, which is
what makes the table above producible at all.

Building the training set from an annotated corpus:

```bash
uv run --package autune-extraction python -m autune_extraction.labeling     --corpus dataset/ami_public_manual_1.6.2 --out dataset/ami
```

It writes `train.jsonl`, `validation.jsonl` and `test.jsonl` in the same format
the evaluation harness reads, and prints the per-class counts of each split.

**The split is by meeting, never by utterance.** Two utterances from one meeting
share a topic, four speakers and a vocabulary, so splitting at the utterance
level scores the model on conversations it has already read.

Meetings are stratified on whether they carry a decision layer — AMI annotates
decisions in 47 of its 139, and those meetings supply almost every `decision`
label — and then each goes to whichever split has the largest shortfall in its
neediest class. Balancing on total count alone leaves the classes uneven,
because decisions are not spread evenly even among the meetings that have them.

A meeting is not divisible, so with seventeen of them in a held-out split there
is a floor on how even this gets. The summary prints the remaining gap rather
than leaving it to be discovered as a surprising validation score.

A file it writes is training data, not an evaluation set. The evaluation set is
drawn from the team's own meetings and is what ADR 0006 measures against; the
format is shared for convenience, not because a score on AMI would transfer.

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

The classifier's macro F1 over the five kinds is what we train against and what
the harness scores, **taken on an evaluation set where utterances that are none
of the kinds appear at their real proportion**. `none` is scored and never
averaged: a none utterance called `decision` is a false positive in
`decision`'s precision, and a decision called none is a miss in its recall.
Action item F1 is derived from it and reported beside the best published figure
for the task, per ADR 0006.

A set of labelled utterances only cannot see what the model does with the rest
of a meeting. On AMI the same model scored 0.655 on one and 0.225 on the
meeting's real distribution, with 1,888 false labels per 2,400 utterances
(#149). The harness warns when an evaluation set has no `none` rows. For AMI,
`python -m autune_extraction.labeling` writes `test.jsonl` as the natural
distribution and `test_closed.jsonl` as the labelled-only split, kept for
comparison with numbers taken before `none` existed.

| Metric | Six weeks | Three months |
| --- | --- | --- |
| Action item F1 | 0.43 — matching the best published AMI result, 43.12 (ADR 0006) | above it |
| Classifier macro F1 over the five kinds, `none` present | set in week 2 from the AMI dialogue-act literature, once the evaluation set exists | above it |
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
- `GET /action-items/{id}` is the only route in this module that returns
  utterances verbatim: the drawer asks for one item's quotation when it opens,
  and the list returns utterance ids. The list is still meeting content — an
  item's `description` is drawn from what was said and `assignee_label` is a
  person's name — so no response of this module may be forwarded outside our
  infrastructure on the grounds that it quotes nobody. `check_outbound` catches
  the shapes of personal data, not a Korean name or the sentence that settled a
  decision.

## Open questions

- Whether Jira sync is per-action or batched per meeting.
