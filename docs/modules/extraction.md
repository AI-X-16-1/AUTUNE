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
agreement, and sync the result to Notion.

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
| Notion | Issue creation via `packages/integrations` | — |
| Slack | Action-item card thread, confirmation DMs | — |

## Pipeline

1. **Classify** — a fine-tuned DeBERTa classifier (`kakaobank/kf-deberta-base`,
   see "AI stack") over each utterance, in spoken order: `commitment`,
   `decision`, `open_question`, `concern`, `ambiguous`, or **`none`** — most of a
   meeting is none of them (#149).
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
   **Noun-ended wording (`noun_form.tidy`).** What is stored as an item's
   description and a decision's statement is the sentence tidied into the form a
   record uses: "그럼 제가 다음 주 화요일까지 볼게요" becomes "다음 주 화요일까지 볼
   예정", "A안으로 진행합시다" becomes "A안으로 진행함". A fixed list of endings
   and a few fillers, not a model; a sentence with a negation or a question in it,
   or an ending the list does not know, is kept as it was said. The original
   utterances stay in `ext_*_sources` and are shown beneath the line — the
   drawer's "근거 발화" for an item, the row's "원본 발화" for a decision — so
   the person confirming reads one against the other. Only the tidied line, as
   the person confirmed or reworded it, leaves Autune: an item goes out only
   after it leaves *needs confirmation*, a decision only once confirmed, and
   never the original utterance. With `resolver_impl=llm` the description is also
   a **summary**: the model reads the commitment, the lines around it and up to
   eight lines from elsewhere in the meeting that share its subject (found by
   word overlap, `pipeline/related.py`), writes one sentence, and says which
   lines it used. Those lines are stored (`ext_action_item_related`) and shown
   beneath the summary as "요약에 쓴 발화", above the quotation, so a person can
   check the sentence against what it was made from and correct it — the
   description is editable like any other. The candidates are only offered: a
   line nobody cites is neither stored nor shown, and a citation the model
   invents (a number that is no line, the commitment itself) is dropped.
   A decision is written up the same way (`ext_decision_related`, "요약에 쓴
   발화" on S15) -- but only when its settling turn does not say what was decided:
   short, or pointing at something said before ("그렇게 하죠"). Asked about every
   decision, the model rewrote all of them and cited a line for about a quarter;
   the rest it only put into "~하기로 했습니다", which `noun_form.tidy` does without
   a model.

   **What module D is sent is not what the screen shows.** `ext_decisions.statement`
   is the line a person sees and that leaves for Notion -- noun-ended, or the
   write-up. `original_statement` is the sentence as assembled from the utterances
   (the turn that settles it, plus owner and deadline), and that is the
   `Decision.statement` in the contract, unless a person reworded the decision, in
   which case it is their wording. D embeds statements and compares them against a
   similarity threshold tuned on that shape (`context.config`), so nothing made for
   the screen may reach it; D reads the utterances themselves through
   `source_utterance_ids` as before. The contract is unchanged. A sentence that names nothing ("다음 주
   화요일까지 볼 예정") is read with up to three lines said just before it, shown
   apart from the sources as "앞선 발화 (맥락)"; nothing fills the missing object
   into the line itself unless the reference resolver is switched on
   (`resolver_impl`, off by default). The due date is still read from the original
   words, which carry the verb ending it depends on.
4. **NLI verification** — check whether an apparent agreement entails an actual
   commitment. Weak assent ("한번 볼게요") is labeled `ambiguous`.
5. **Build decision entities** — group the utterances classified as decisions
   into `Decision` records with a `dec_` id and the statement as settled. One
   decision often spans several utterances. **Module D depends on this**: it is
   what a decision lineage is keyed on, and a `Classification` alone is not
   enough. See `../architecture/contracts.md`, "The B → D boundary".
6. **Confirm** — every ambiguous agreement is recorded in `ext_confirmations`
   first, then the speaker gets a Slack DM. Until the DM goes out the row is
   *not asked* and `AmbiguousAgreement.confirmation_sent` is false. Every five
   minutes `ask_confirmations` asks each one recorded within the 72-hour window
   whose speaker is identified and consented, through the team's Slack bot to
   the account that person linked (#255, #478), and to nobody else. A team
   without Slack, or a speaker who has not linked, is looked at again on the
   next run until the window closes.
   **What the answer does.** *Commitment* makes one draft item for that
   utterance, slot-filled like any commitment (the speaker is the assignee, the
   first date phrase the due date, the utterance's own text — tidied into the
   noun form, as in step 3 — the description),
   in *needs confirmation* with confidence 1.0 — the speaker's answer is the
   certainty, and the team still accepts the item before it leaves for Notion
   or a calendar. Any other answer makes no item; a later answer replaces an
   earlier one, so changing *commitment* to *not a commitment* takes the draft
   back unless a person has moved or edited it since. A rerun of the meeting
   keeps the draft (it is derived from `ext_confirmations` again) and never
   makes a second one. `ext_classifications` is not rewritten: it records what
   the model said and `resolved_kind` what the speaker said, and the two stay
   comparable.
7. **Sync** — when a person confirms an action item (moves it out of
   `needs_confirmation`), create one page for it in the team's Notion database
   and store the URL in `ext_external_refs` (#30). One page per item: a later
   edit updates it (#342). If someone deletes that page in Notion, the next edit
   makes a new one; if someone archives it, it is left archived (#403). The
   board does not say so yet: later edits to that item stop reaching Notion
   and only the log records it. S18's integration row is where an "archived in
   Notion" state belongs once it exists. A team without Notion connected is
   skipped. Not
   part of the extraction run: nothing the model drafted is confirmed yet (#246).
   A decision goes the same way when a person confirms it (or adds it), to the
   team's decision database, in the wording they confirmed
   (`ext_decision_refs`).
   A confirmed item with a due date also goes on its **assignee's own Google
   Calendar** as an all-day event with no attendees, through that person's grant
   in `user_integrations` (#435, #444); team work is not copied into anyone's
   calendar. Every ten minutes `pull_calendar_changes` reads back Autune's own
   tagged events on each connected calendar, and a date the person moved there
   becomes the due date through the board's edit path (`ext_calendar_events`,
   `ext_calendar_polls`).
   A confirmed item is also one issue in the team's Jira project (#82, #458),
   and every ten minutes `pull_jira_changes` reads back the status people moved
   their issues to: an issue dragged to Done makes its item done, through the
   same edit path. `ext_external_refs.synced_category` records what Autune last
   left the issue in, so a board edit that has not reached Jira yet is never
   undone; when both moved, the board wins. A ref with no baseline yet (made
   before the read-back, or its issue never took the board's status) gets
   Jira's category recorded as one, and the board is left alone.
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
| `ext_decision_related` | The other lines of the meeting a decision's summary was written from, as the model said it used them; shown beneath the summary, never read by D |
| `ext_action_item_related` | The other lines of the meeting the item's summary was written from, as the model said it used them (`LlmResolver`); shown beneath the summary, never read by D or E |
| `ext_edit_events` | One row per correction. Counts only — no person on it |
| `ext_external_refs` | The Notion page an action item became, one per item and system |
| `ext_decision_refs` | The Notion page a confirmed decision became, one per decision and system |
| `ext_calendar_events` | The event an item's due date became on its assignee's own calendar, and the date last synced |
| `ext_calendar_polls` | When each person's calendar was last read back |
| `ext_notion_targets` | The page and three databases a team's Notion sync writes to, one row per team (#428) |
| `ext_confirmations` | Every ambiguous agreement, the DM once sent, and the response |
| `ext_decisions` | Decision entities, their statements and source utterances. `origin` is `model` or `user`; a rerun rebuilds only the model's |
| `ext_decision_sources` | Which utterances a decision was settled in, in order |
| `ext_decision_reviews` | A person's verdict on each proposed decision (pending, confirmed, rejected) and an optional rewording, keyed by `dec_` id so a rerun over the same sources keeps it (#246). No reviewer column |
| `ext_extraction_runs` | One row per extracted meeting: a digest of the consenting utterances the last run read, and when (#518) |
| `ext_meeting_notes` | The team's memo on a meeting's summary tab (S15 요약, #421). Free text a member typed; no author column; a blank memo is no row |

**The summary tab (S15 요약, #421, WBS 4.9).** B owns it. v1 is structured and
uses no model: `GET /summary/{meeting_id}` gives the meeting's decisions
(confirmed first, then pending; rejected left out), every action item, how many
open questions were asked and how many ambiguous agreements still wait for
their speaker, and the team's memo (`PUT /summary/{meeting_id}/note`, whole
memo, blank removes it). The tab reads it in three levels -- counts, then the
decisions and items, then their source lines on the 액션 tab. Nothing leaves,
so it serves real meetings whatever #392 decides. A prose summary by an LLM
over the whole meeting -- chunk summaries under the outbound limit, then a
summary of those -- is v2 and waits on #392.

A meeting that is processed again replaces its model-made rows —
classifications, decisions, and draft items — rather than adding a second set,
which is what makes a redelivered task safe. The one exception is the draft:
once a person has edited anything in the meeting, a rerun leaves its items
alone, because ADR 0006 makes the list theirs to finish.

**Consent that changes after the run (#518).** The consent filter reads
`participants.consented` when the run starts, and consent can be recorded
later (A's `attest_consent`). Nothing announces that (#360), so every ten
minutes `reextract_consent_changes` compares each meeting's
`ext_extraction_runs` digest with the consenting utterances now, and extracts
the meetings that differ again from the stored transcript — the same run as
the event's, so it follows the rules above and publishes `ExtractionResult`
again. A meeting extracted before the table existed has no row and is left
alone. Speech that loses consent drops out of the model's rows the same way;
what a person already edited or sent out from it waits on per-person
withdrawal (S10/S11), the second half of #518.

**A speaker identified after the run (#360).** A commitment by an unidentified
speaker keeps only the label ("Speaker 2"). When A later fills
`participants.user_id`, nothing announces it, so every ten minutes
`fill_identified_assignees` gives each model item from the last 30 days still
holding only the label it was drafted with the account of the one identified,
consenting speaker behind its sources, and clears the label. An item whose
assignee a person may have edited -- an edit naming an assignee field, or an
older edit row naming no fields -- or whose label a person renamed is left
alone. The fill is write-once: a later re-identification of the speaker is a
person's reassignment on the board. A confirmed item is synced to Notion,
Jira and the calendar the way the router syncs a board edit; like a board
edit, no `ExtractionResult` is published.

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
| GET | `/action-items/{id}` | One item, the text of its source utterances in spoken order, up to three lines said just before them as `context`, and the lines its summary says it used as `related` (consenting speakers only) |
| PATCH | `/action-items/{id}` | Edit or close an item |
| POST | `/action-items` | Add an item the model missed |
| DELETE | `/action-items/{id}` | Delete an item the model got wrong |
| POST | `/results/{meeting_id}/sync` | Re-sync to Notion — not built; confirming an item syncs it |
| GET | `/reviews/{meeting_id}` | What needs a person before anything is sent: decisions with their verdict, weak assents with their DM state, items still `needs_confirmation` or below the candidate line (S15, #246) |
| POST | `/decisions` | Add a decision the model missed. Confirmed, and kept through reruns |
| GET | `/decisions/{id}` | One decision and the text of the utterances it was settled in, in spoken order (S15 shows them beneath the statement), plus the same `context` |
| PATCH | `/decisions/{id}` | Confirm, reject, reword, or put back to pending |
| DELETE | `/decisions/{id}` | Delete a decision a person added; reject one the model proposed, which a rerun would otherwise bring back |
| GET | `/reviews/{meeting_id}/outbound` | Exactly what may leave for Notion or Slack: confirmed decisions and accepted items, each screened for personal data (a hit is held back in `blocked`, by id and category). The sync reads this and nothing else |

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |
| `autune.extraction.on_transcript_ready` | `autune.transcript.ready` | `cpu_heavy` |
| `autune.extraction.sync_action_item` | A person confirms an action item (`PATCH /action-items/{id}` out of `needs_confirmation`). Today it runs in the API process right after the response, as a FastAPI background task — apps/api builds no Celery app to queue it on | `default` |
| `autune.extraction.sync_decision` | A person confirms a decision (`PATCH /decisions/{id}` to `confirmed`) or adds one (`POST /decisions`). Runs in the API process after the response, like `sync_action_item` | `default` |
| `autune.extraction.send_confirmations` | After extraction | `default` |

## Slack surface

- Action-item card thread posted to the meeting channel
- A DM to each speaker with an ambiguous agreement, asking for confirmation
- Role-specific reports (Phase 2)

## AI stack

| Component | Model |
| --- | --- |
| Utterance classification | `kakaobank/kf-deberta-base` (DeBERTa, [MIT](https://huggingface.co/kakaobank/kf-deberta-base)), fine-tuned |
| Agreement verification | `klue/roberta-base` fine-tuned on KorNLI ([CC BY-SA 4.0](https://github.com/kakaobrain/kor-nlu-datasets) training data, server-only — #172) |
| Reference resolution, report generation | LLM |
| Due-date parsing | Rule-based Korean date parser plus LLM fallback |

Target non-LLM share is roughly 60%: classification and verification are models
we train, not prompts.

### Which encoder, and what is still open

The encoder is `kakaobank/kf-deberta-base`, chosen in #112 over
`microsoft/mdeberta-v3-base`. The two share an architecture (12 layers, 768
hidden, 12 heads) but not a cost. Measured on CPU (6 threads) over 256 Korean
utterances in batches of 32, `max_length` 96:

| | kf-deberta-base | mdeberta-v3-base |
| --- | --- | --- |
| Forward pass, one batch of 32 | 1.63 s | 29.9 s |
| Throughput | 19.6 utterances/s | 1.1 utterances/s |
| One 45-minute meeting (~2,400 utterances) | 2.0 min | 37.4 min |
| Tokens per Korean character | 0.485 | 0.673 |

It also trains on the English AMI data despite being a Korean model: 4,000 AMI
utterances for two epochs reached a five-way macro F1 of 0.6082 on English AMI
(#112), so the AMI loader and label mapping are not wasted.
`training.BASE_CHECKPOINT` holds the name, and `--base` overrides it for a
comparison run.

**The encoder is decided; the trained checkpoint is not.**
`AUTUNE_EXTRACTION_CLASSIFIER_CHECKPOINT` stays blank until a Korean-trained
checkpoint beats the AMI-only baseline on the Korean evaluation set (#10). Where
that checkpoint is stored, and under which terms, is open on #112. AMI is
CC BY 4.0 and needs attribution. Rows derived from AI Hub carry AI Hub's own
terms, which govern providing the data to others and taking it abroad.

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

- Only what an issue needs goes to Notion: the action description, assignee,
  and due date. Never the full transcript.
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
