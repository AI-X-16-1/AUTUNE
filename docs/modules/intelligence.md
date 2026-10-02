# Module E. Meeting Intelligence

| | |
| --- | --- |
| **Package** | `autune_intelligence` |
| **Owner** | 이승환 |
| **Backend** | `modules/intelligence/` |
| **Frontend** | `apps/web/src/features/dashboard/` |
| **Table prefix** | `intel_` |
| **API prefix** | `/api/intelligence` |

## Responsibility

Aggregate across meetings and across modules. Score meeting quality, classify
gap patterns, predict misalignment, produce the team dashboard and the weekly
report, and deliver each participant their own speaking ratio.

## Non-goals

- Producing any of the raw signals. E consumes B, C, and D through contracts and
  never recomputes their work.
- Per-meeting user-facing output. E is the layer that appears once data has
  accumulated.

## Inputs

| Source | Contract |
| --- | --- |
| B | `ExtractionResult` via `autune.extraction.completed` |
| C | `GapReport` via `autune.gap.completed` |
| D | `ContextLinks` via `autune.context.completed` |
| B | `TeamActionProgress` via `autune.extraction.action_progress` -- a team's action-item counts per meeting, every ten minutes (#605) |
| `packages/core` | `meetings`, `participants`, `utterances` (read-only) |

## Outputs

| Destination | Contract | Event |
| --- | --- | --- |
| `apps/web`, `apps/bot` | `IntelligenceSnapshot` | `autune.intelligence.completed` |
| agent layer (Report subagent) | `Payload` (the meeting id only) | `autune.intelligence.meeting_report_changed` -- a person edited a report's draft (#674) |
| Slack DM | Personal speaking ratio, to that person only | — |
| Slack channel | Weekly report, prediction warnings | — |

## Partial results

E is the only module that depends on three others, and any of them can fail. It
must never block on a missing source.

- Track completion per meeting for B, C, and D.
- Aggregate when all three arrive, or when the timeout elapses (10 minutes by
  default).
- Mark which sources were missing in the snapshot rather than omitting the
  snapshot.

See `../architecture/async-pipeline.md`.

## Pipeline

1. **Collect** — gather whatever of `ExtractionResult`, `GapReport`, and
   `ContextLinks` has arrived.
2. **Quality score** — grade A–F from decision density, gap count, action-item
   completion rate, and participation balance.
3. **Gap classification** — SetFit classifies each gap's `Gap.title` (not
   `Gap.category` — C's category is free text whose vocabulary is not stable
   across meetings, the reason this step exists at all, and mixing it into the
   classified text measurably hurts confidence on inputs the seed set never
   trained on that shape) into one of a fixed pattern-type vocabulary
   (`pipeline.base.PATTERN_TYPES`). `intel_gap_patterns` records which
   classifier version produced each row and the mean confidence over that
   pattern's gaps (`avg_confidence`). Selected by
   `AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL` (`local` or `fake`); `local` fits
   its few-shot head from a seed example set checked into `pipeline.classifier`,
   not an evaluated corpus — revisit once real `GapReport` traffic exists to
   check it against. `AUTUNE_INTELLIGENCE_WARM_MODELS_ON_WORKER_INIT` fits that
   head at worker startup instead of inside a meeting's aggregation lock. See
   `docs/engineering/environments.md`.
4. **Alignment** — pairwise cross-role agreement, producing the heatmap
   (`alignment.py`). Input is only `Decision.stance_by_role` from B — counts
   per role, already gated by the contract (three identified people, no
   unanimous role; `../architecture/privacy.md` section 3). Per decision, a
   role's position is `(supporting - concerns) / identified` in `[-1, 1]`, so
   people who said nothing count as neutral; two roles agree by
   `1 - |pos_a - pos_b| / 2`. A decision where *neither* role expressed a
   stance is skipped, so silence does not read as consensus. A meeting's score
   for a pair is the mean over the decisions both roles have a row on, written
   to `intel_alignment` (unordered pair, `role_a < role_b`) and to the
   snapshot. The heatmap averages per-meeting scores and **leaves out any pair
   scored in fewer than three meetings** (`MIN_MEETINGS_PER_HEATMAP_CELL`,
   privacy.md section 3: a small sample leaves the cell empty). Until B's
   stance producer ships (#10, #168), `stance_by_role` is always empty and so
   is the heatmap.
5. **Prediction** — `misalignment_risk`, 14 days: the probability that a
   decision this meeting settled is **reversed** within two weeks. D already
   records reversals (`DecisionChange.change_type == "reversed"` with the
   earlier meeting on `previous_meeting_id`), so that is also the training
   label — see Metric below. `prediction.meeting_features` reduces the meeting
   to meeting- and role-level numbers (quality value, gap counts, weakest
   role-pair alignment, D's lineage churn, whether a change happened with a
   key stakeholder absent — a boolean, never who — ambiguous agreements,
   unconfirmed actions, missing sources); `None` means not measured. The
   predictor is chosen by `AUTUNE_INTELLIGENCE_MISALIGNMENT_PREDICTOR_IMPL`:
   `heuristic` (default) is a logistic over hand-set weights — unvalidated,
   the same P1 status as the quality-score weights (#26); `local` fits XGBoost
   on the trailing 12 weeks of labeled meetings (#27's trailing window) and
   refits every `AUTUNE_INTELLIGENCE_MISALIGNMENT_REFIT_HOURS`, keeping the
   heuristic until there are 50 labeled meetings with at least 5 of each
   outcome. Every prediction is stored in `intel_predictions` with its
   `model_version`; it is **shown** — in the snapshot and on `/predictions` —
   only once the team has four weeks of history and three scored meetings
   (#27). Prophet trend forecasting is not built; see Open questions.
6. **Report** — `service.generate_weekly_report` aggregates `intel_scores` and
   `intel_gap_patterns` for a team over `[period_start, period_end)` into one
   `intel_reports` row (upserted by `(team_id, period_start)`), and
   `tasks.generate_weekly_report` posts it to the team's Slack channel.
   `period_end` defaults to today, `period_start` is 7 days before it. The
   body is **not** LLM-generated yet — `_report_body_markdown` is a
   deterministic template over the same numbers, with LLM prose deferred: no
   shared LLM client exists in `autune_integrations`, and a module-local one
   was rejected in review for module D (PR #90) because it bypassed
   `check_outbound`. The report row is written whether or not Slack is
   connected, so `GET /reports/{team_id}` has something to serve either way;
   only the channel post is skipped without a connected Slack or a `channel`
   key in its `config`. **Nothing calls this task on a schedule yet** — there
   is no Celery Beat wiring in `apps/worker`, and none of the other four
   modules have one either. Scheduling is a separate, cross-cutting decision,
   not made here.
7. **Personal feedback** — compute each participant's speaking ratio and DM it
   to that person. Do not store it. The ratio is a share of *measured* speech:
   the denominator is speech attributed to participants who consented to
   speaker attribution, and unattributed speech is excluded. Speech is grouped
   by person (`user_id` once identified, falling back to `participant_id`
   otherwise) rather than by participant row — diarization can split one real
   speaker across two `participants` rows that later resolve to the same
   `user_id`, and counting those as two people would let the ratio arithmetic
   below leak the real person's exact share to whoever else was in the room.
   The even-share baseline in the DM (`100 / participant_count`) is taken over
   every *consenting* participant, silent ones included — a silent participant
   is still part of the room the even share is measured against, so the ratio
   and the baseline deliberately answer different questions ("my share of what
   was said" vs. "my share of an even split of the room"). A "share of the
   whole meeting" denominator was rejected: any unattributed speech would push
   every participant below a baseline none of them could reach.

   **The ratio is withheld when fewer than three people spoke.** Because the
   measured shares sum to 100%, when only two people's speech is in the
   denominator one person's ratio fixes the other's exactly — the response
   would then *contain* another person's speaking ratio, which
   `../architecture/privacy.md` section 3 forbids, and an above/below-baseline
   band does not help because with two the two mirror each other. The gate
   counts distinct *identified* people who spoke, not the consenting head
   count and not participant rows: three consenting participants where one
   only listened still splits its speech two ways, and one real speaker split
   across two participant rows by diarization counts once *once identified* —
   one label resolves to the same `user_id` as the other. Before
   identification a split cannot be merged by `user_id`, and an unidentified
   label cannot be told apart from "the unconfirmed other half of a speaker
   who is already identified" either, so an unidentified label counts as
   **zero**, not one and not "at most one" — any positive count for it can
   overstate the real population by exactly the amount the N=2 guard exists to
   catch. The cost: a genuine three-person meeting with one speaker not yet
   identified reads as too small until identification finishes — since this
   is recomputed on every request rather than cached, it self-corrects once
   that happens rather than needing a retry. In that case `GET
   /me/speaking-ratio` returns `ratio: null` with
   `reason: "small_meeting"` (distinct from the `404` for someone who was not in
   the meeting), and no DM goes out. A participant who did not consent to
   attribution gets `ratio: null` with `reason: "not_measured"` — distinct from
   a consenting participant who was silent, who gets `0.0`.
8. **Publish** — emit `IntelligenceSnapshot`.

## Tables

| Table | Purpose |
| --- | --- |
| `intel_scores` | Per-meeting quality score and its components |
| `intel_gap_patterns` | Gap classifications over time |
| `intel_alignment` | Role-pair alignment scores |
| `intel_predictions` | Predictions with horizon and probability |
| `intel_reports` | Generated weekly reports |
| `intel_completion` | Which of B, C, D have reported per meeting |
| `intel_meeting_reports` | One summary report per meeting, composed by the agent layer's Report subagent; posted once, deleted with its meeting |
| `intel_action_progress` | The latest `TeamActionProgress` per team: its `as_of`, kept even when it listed no meeting so "none confirmed" stays apart from "unknown" |
| `intel_action_progress_meetings` | That snapshot's counts per meeting (confirmed, done, overdue); deleted with its meeting; shown only as team totals |

There is no speaking-ratio table, and there will not be one.

E stores IDs referencing B's and C's outputs as plain string columns — never as
foreign keys to another module's tables.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/dashboard/{team_id}` | Dashboard data |
| GET | `/scores/{meeting_id}` | One meeting's quality score |
| GET | `/heatmap/{team_id}` | Cross-role alignment heatmap; pairs with fewer than three meetings left out |
| GET | `/predictions/{team_id}` | Latest misalignment prediction, or `null` with a reason before #27's gate clears |
| GET | `/gap-titles/{team_id}` | High-severity gap titles behind each pattern count |
| GET | `/reports/{team_id}` | Weekly reports |
| GET | `/meeting-reports/{team_id}` | The team's meeting reports for the dashboard card: header line, body, draft/posted, editor. **Team members only** |
| PUT | `/meeting-reports/{meeting_id}` | A team member edits a draft's body before it is posted (send back `base_updated_at`; a newer save makes it 409). Editor and time recorded, E's footer says a person edited it (the name is added from `edited_by` when read or posted, never stored), and the draft takes a **new `draft_id`**, so the approval given for the model's text lapses. Committed, then announced on `autune.intelligence.meeting_report_changed`; the Report subagent proposes the edited draft's post for approval. Nothing is posted from the card. 409 once posted, 422 with categories for personal data or over 3,000 characters as Slack receives it, 404 for anyone outside the team |
| POST | `/meeting-reports/{meeting_id}/corrections` | A member corrects a **posted** report whose post reached Slack (202). It waits for approval like an edit: committed, then announced, and the Report subagent proposes `publish_meeting_report_correction` with its `correction_id`; once approved it goes out as a reply under the post. A newer correction replaces one still waiting. 409 for a draft, for a report that never reached Slack, or while an approved correction is being posted (at most five minutes); 422 for an unchanged, empty, too long or personal-data correction |
| GET | `/me/speaking-ratio/{meeting_id}` | **The requester's own ratio only** |

**Meeting reports on the dashboard (#642, #674).** Every post goes through
approval (L2), a person's edit included: a report goes out under the bot's name
to the whole channel, and #592/#621's approvers decide that. An edit lapses the
approval given for the model's text (new `draft_id`) and is announced from the
worker (`announce_meeting_report_changed`; the API process cannot publish,
#170). The Report subagent wakes on the event, reads the draft E holds now
(`meeting_report_awaiting_approval`) and proposes its post without rendering
anything; plan mode supersedes the earlier post proposal for the meeting, so
the approver sees one card, for the text that is there. A rerun of the Report (a
republished `intelligence.completed`, #556) overwrites an edited draft and
clears its editor -- the corrected inputs win, and the card stops showing a
name on text that person did not write. The "this meeting only" rule (#459)
is an instruction to the subagent; a person's text is not checked against it.
The editor's name is not stored with the text: the stored footer reads
"팀원이 고쳤습니다", and the name is joined from `edited_by` when the card
reads the report and when the worker claims it, so it goes with the account
(invariant 11). A name that looks like personal data is left out rather than
refusing the post. Report text is
escaped for Slack (`&`, `<`, `>`), so a mention or a disguised link in it goes
out as plain text, and the 3,000-character cap counts the escaped text. A
posted copy in Slack is outside Autune: deleting an account or a meeting does
not recall it.

**A correction to a posted report (#658, #674).** A posted report is never
changed in place. A member writes a correction on the card; it goes the same
way as an edit -- announced, proposed by the Report subagent as
`publish_meeting_report_correction` with its `correction_id`, and posted only
after a `report` approver approves it -- as a reply under the original post,
"✏️ 수정본 · <time> · <name>", or as a new message in the team's channel when
that thread is out of reach (a reconnected workspace). Accepted only for a
report whose post reached Slack. The approval pins the `correction_id`: a
correction written after it replaces the waiting one, and that approval then
posts nothing. Statuses: pending (waits for approval), sending (claimed, being
posted), sent, failed (claimed but not posted within
`CORRECTION_SEND_WINDOW`, five minutes from the claim -- a new one is then
accepted). Each is checked and escaped like the report and posted at most once.
Once the report is posted, a late `intelligence.completed` run of the Report
subagent proposes nothing (E's read answers `already posted`), so it cannot
supersede a correction waiting for approval.

**No change is left without an approval request (#698).** The card's routes
commit a change, then queue its announcement; a queue that refuses it does not
fail the request. An announcement claims the change it covers (`announced_at`,
under the row lock, before it publishes; a failed publish gives it back), so
the route's task, the sweep and a redelivered task never announce one change
twice -- each announcement notifies the approvers.
`autune.intelligence.periodic.announce_report_changes` announces, every five
minutes, any edit or waiting correction older than two minutes that no
announcement covered; one meeting's failure does not stop the rest. A save that
changes only trailing spaces or blank edges counts as unchanged. A correction is
refused while the team has no Slack token or channel, and an approved one whose
team lost Slack after the post reads as
failed (`correction_failed_at`) rather than waiting forever; the next
correction, after reconnecting, clears it.
Only the text, its id and `corrected_by` are stored; the name is joined when it
is read or sent, as for an edit. The approval card can read it through
`meeting_report_correction(correction_id)`. Deleting a post from Slack is not
built: it needs `chat.delete` in `packages/integrations` and a decision on who
may do it.

`/me/speaking-ratio` authorizes on `requester_id == subject_id`. There is no
admin override and no team-level variant of this endpoint.

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |
| `autune.intelligence.aggregate` | B, C, D completion or timeout | `cpu_heavy` |
| `autune.intelligence.on_extraction_action_progress` | B's `TeamActionProgress`, every ten minutes | `default` |
| `autune.intelligence.send_personal_feedback` | After aggregation | `default` |
| `autune.intelligence.weekly_report` | Weekly schedule | `cpu_heavy` |
| `autune.intelligence.deliver_meeting_report` | An approved post (`publish_meeting_report`, L2) | `default` |
| `autune.intelligence.announce_meeting_report_changed` | A person's edit or correction on the dashboard card, after it commits (#674) | `default` |
| `autune.intelligence.periodic.announce_report_changes` | Every five minutes: changes no announcement covered (#698) | `default` |
| `autune.intelligence.deliver_meeting_report_correction` | An approved correction (`publish_meeting_report_correction`, L2) | `default` |

## Slack surface

- Weekly insight report to the team channel
- Prediction warnings when misalignment risk crosses a threshold
- Personal speaking-ratio DM: "이번 회의에서 당신의 발언 비중은 12%였습니다"

## AI stack

| Component | Model |
| --- | --- |
| Gap pattern classification | SetFit (few-shot) |
| Prediction | Heuristic baseline; XGBoost once labeled history exists |
| Trend forecasting | Prophet — not built |
| Label efficiency | Active learning |
| Report generation | LLM, from computed numbers only |

## Metric

Prediction calibration, reported by the owner. Dashboard metrics are descriptive
and have no accuracy target; the prediction does.

```bash
uv run --package autune-intelligence python -m autune_intelligence.eval [--window-weeks 12] [--json]
```

A meeting is labeled positive when a later meeting's `decision_lineage`
reverses one of its decisions within 14 days, and is labeled at all only once
those 14 days have passed. It is also left unlabeled when a meeting of the same
team inside those 14 days could not have shown a reversal — either its lineage
was measured and came back without B's decisions (D publishes that way when B
times out), or E never aggregated it at all, so nothing was measured. Either way
a reversal there would have been invisible and "negative" would claim more than
anything looked at. A meeting already seen
to be reversed stays positive. Meetings held back this way are counted in the
`intelligence_history_labels_blocked_by_blind_spot` log line, so "0 labeled
meetings" can be told apart from short history. History is read back from E's own tables, and
features are rebuilt with the same `meeting_features` the live path uses. The
report gives, per model version — both what was stored and shown
(`stored:<version>`) and the current predictor over the same meetings
(`current:<version>`) — Brier score against a constant base-rate baseline,
log loss, expected calibration error and a reliability table. Fewer than 30
labeled examples prints "not scored" instead of a number.

`current:*` is produced by the predictor this process would use, so a
`current:xgb-*` row appears only under
`AUTUNE_INTELLIGENCE_MISALIGNMENT_PREDICTOR_IMPL=local`. When that predictor is
one fit from history, the report **holds out the recent past**: it rebuilds the
predictor as of `now - holdout`, so the fit sees only meetings whose horizon had
closed by then, and scores it only on meetings after that point. The row says
`(out of sample)`. `--holdout-weeks` sets it; the default is four weeks, and it
has to exceed the 14-day label horizon or nothing is left to score.

Without the holdout the fitted model was scored on the window it was fit on. On
200 meetings whose reversals were drawn independently of every feature — so the
honest skill is zero at best — that procedure reported a Brier skill of **+0.682**
where the held-out score was **-0.120**: it would have promoted a model that had
learned nothing.

**Two things must hold before the default becomes `local`.** The first is about
whether the comparison can be trusted at all; only the second is the comparison.

1. **#450 is fixed.** The two rows below are scored on different meetings:
   `current:` on the holdout window alone (a fortnight under the defaults),
   `stored:` on the whole training window. Brier skill normalises each against its
   own base rate, so a difference in reversal rate does not decide it — but
   nothing corrects for a fortnight simply being easier to call than a quarter.
   #450 adds a `stored:` row over the same holdout meetings, which is the
   comparison this rule means.
2. **`current:xgb-*` beats `stored:heuristic-v1` on Brier skill**, over the same
   meetings once #450 lands. Compare the skill, not the raw Brier.

Until both hold, the default stays `heuristic`.

There was a third condition until #453: the label treated a meeting E had never
aggregated as evidence of no reversal, so both the fit and the comparison would
have rested on false negatives. That is fixed, and the blind spot it left behind
is described below.

Known blind spots in the label itself:

- A decision modified and then reversed in a third meeting names the modifying
  meeting, so the original stays negative.
- A later meeting E has not aggregated withholds the earlier meeting's label
  the same way a measured one with a missing extraction source does, but the two
  need different fixes — B not reaching D, against a pipeline that stalled or
  failed before E saw the meeting. So the blind-spot log reports
  `blocked_by_unmeasured_lineage` and `blocked_by_unaggregated`. Both count
  **withheld meetings**, not blind spots: one stalled meeting can withhold
  several labels or none, and a count of blind spots would send someone after one
  that is doing no harm. A meeting can be withheld by both causes, so the two
  need not sum to `meetings`.

## Privacy notes

E is the module where privacy is easiest to break, because aggregation is
exactly what a surveillance feature looks like. Read
`../architecture/privacy.md` section 3 before building anything here.

- **Speaking ratio goes to the speaker and nobody else.** Not to a manager, not
  to the meeting organizer, not to an admin, not to an export.
- **Per-person speaking ratios are not stored.** Compute, deliver, discard.
- **Speaking ratio never enters `IntelligenceSnapshot`** or any other contract.
- **No small-group distributions.** A speaking-time distribution over a
  four-person meeting identifies everyone in it; anonymization does not help.
- Team-level metrics — quality score, alignment, gap distribution — are
  aggregate by construction and contain no per-person speech volume.
- **The influence map (Phase 2) goes to the person themselves and nobody
  else — decided on #28.** Proposal adoption rate, decision dominance, and
  interruption patterns are per-person behavioral metrics, and unlike
  `stance_by_role` they accumulate across meetings: a role-level rollup would
  keep exposing the same people's patterns for as long as the role's roster
  holds, getting easier to re-identify over time rather than harder. Deliver
  it the way S23 delivers speaking ratio (subject only); it does not appear on
  the shared dashboard (S26).

## Open questions

- Quality score weighting: fixed weights or learned from user feedback.
- Prophet trend forecasting. `Prediction` carries a probability, so a trend
  needs a probability-shaped question (for example "the team's weekly quality
  average falls a grade within four weeks"), and a weekly series has only a
  handful of points for the first months — too few for Prophet's seasonality
  to mean anything. Deferred until there is enough history to evaluate it.

Decided: predictions are shown after four weeks of history and three meetings
(#27).
