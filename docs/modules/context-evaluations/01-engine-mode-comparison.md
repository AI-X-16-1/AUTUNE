# Evaluation 01 — the trained stack against an external LLM

**Measured:** 2026-09-30 and 2026-10-01 · **Written:** 2026-10-01 · **Owner:** 문민재 ·
**Module:** Meeting Context Engine (`autune_context`)

The question: *can an external LLM make the Meeting Context Engine's judgements
better than the trained stack, and is it worth what it costs?* Three judgements
are in play — is this past meeting the same topic, which decision thread does a
new decision join, and how did that decision change — and `engine_mode` (see
[`../context.md`](../context.md), "Engine mode") decides who makes them:
`classic` (re-ranker, similarity thresholds, NLI, lexical cues), `llm`, or
`hybrid` (classic, with the LLM able to veto a topic link classic is about to
assert; decision lineage stays classic).

The short answer: **on a held-out set the LLM comes out ahead on both suites, the
two sides fail in opposite directions, and neither lead is statistically
established.**

- **Topic linking** — `classic` 0.90, `hybrid` 0.93, `llm` **0.96**. The LLM asserted
  no false link in 42 chances (`classic`: 2) and missed 4 of 81 true ones
  (`classic`: 4). Against `classic`: nine cases better, three worse (sign test,
  p = 0.15).
- **Decision lineage** — `classic` 0.81, `llm` **0.90**. `classic` reads every
  replacement that carries no cue word as a parameter change: 0 of 10. The LLM
  gets all ten, and all 27 `reversed` — and in exchange calls nine real
  modifications `reversed`. Eighteen cases better, nine worse (p = 0.12).
- **Decision lineage dropped for both modes on the new set** (`classic` 0.98 → 0.81,
  `llm` 0.98 → 0.90): it is harder than the development set, and `classic`, which
  was tuned on that set, lost the most. Topic linking barely moved (`classic`
  0.92 → 0.90, `llm` 0.94 → 0.96).
- **One run is missing**: `hybrid` with the floor at `0` on held-out v3, which hit
  the free tier's daily limit. See section 6.
- **Afterwards, `classic` gained a replacement cue** (section 8): lineage on v3
  0.81 → 0.91 with no regression on the earlier sets. **That 0.91 is not a held-out
  result** — the rule was written after reading v3's errors — and it does not mean
  `classic` now matches the LLM.
- **On a clean held-out set (v4, 120 cases) the cue is worth +5 cases, against +10 on v3**:
  `classic` 0.72 without it, **0.76** with it (section 9). That is the number to
  quote for `classic`; v3's 0.91 is not.
- **After two guard gaps were fixed, on a fresh set (v5, 120 cases) `classic` goes
  0.74 → 0.82 with the cue** (10 cases fixed, 1 broken, p = 0.012; section 10). Every
  swap that uses a verb the cue knows is caught; none of the swaps said without one is.

---

## 1. What was measured

| | |
| --- | --- |
| **Sets** | Development set `*_v2` (49 topic cases, 50 lineage cases); held-out `*_heldout_v3` (100 + 100, new — see 2) |
| **Classic stack** | KURE-v1 (dense), BM25 over kiwipiepy, `dragonkue/bge-reranker-v2-m3-ko`, in-house KorNLI checkpoint (`kornli-bf16-v1`); all in-process (`*_local` impls). **Sections 3 to 7 describe `classic` as it was on 2026-09-30, before the replacement cue of section 8** |
| **LLM** | `gemini-3.5-flash-lite` through the Gemini `generateContent` API — the lightest tier; no `temperature` is sent (the client has none: reasoning models reject non-default sampling values), thinking left at the model's default. Prompts `judge-v1`, written before any evaluation data was run through them and not changed since |
| **LLM settings** | `llm_concurrency=1`, `llm_max_tokens=4096`, `llm_snippet_chars=1200`, `llm_topic_candidates=5`, `llm_thread_candidates=3`, `llm_link_threshold=0.5`, `llm_pending_floor=0.25` (`0` where stated) |
| **Hardware** | CPU only. transformers 5.16.1 |
| **Database** | A scratch PostgreSQL database per run, migrated to `heads`, dropped afterwards; each case gets its own team |
| **Harness** | `python -m autune_context.eval [suite] [--dataset FILE] --mode classic\|llm\|hybrid\|both\|all` |

Every number below is **one run**. The LLM is not deterministic (there is no
`temperature` to pin), and two `hybrid` runs of the same dev set differed in one
link the model confirmed once and vetoed once. Significance is an exact sign test
on the cases only one of two modes got right.

## 2. The held-out set, and what it is worth

`*_heldout_v3` was written to be bigger and harder than anything before it:
100 topic-linking cases (42 expecting no link, 81 expected links) and 100
decision-lineage cases (25 `unchanged`, 30 `modified`, 27 `reversed`, 18 `new`).

- **Written before any model ran on it, and frozen.** Nothing — a rule, a
  prompt, a threshold — may be chosen by looking at its failures. The `_about`
  field of each file says so. A fix for what it exposed needs a *new* held-out
  set. **That has already been strained once:** section 4.3 reads the error
  patterns of both modes off this set, so any combination designed from them has to
  be evaluated on a new set, not on this one.
- **No overlap** with the earlier sets: a script refused any meeting text or
  decision statement already used, and five were rewritten.
- **New shapes.** Topic linking gains `large_history` (six to nine past meetings,
  so "ask the LLM about five candidates" is a real restriction) and 16
  nine-utterance `long_meeting` cases. Lineage includes phrasings the lexical
  cues in `pipeline/change.py` are documented to be weak on (a replaced owner,
  an absence that means "smoothly"), labelled by what the decision means.
- **Labels follow `dataset.py`**, whose definition of `reversed` is exactly "replaced
  by an alternative that excludes it (MySQL → PostgreSQL, outsourced →
  in-house)". One case whose label could be read two ways was rewritten before
  any run.

**What it is not.** It was written in a single sitting by the same author that
built the toggle, and no second person labelled it. It is synthetic, short, and
Korean-only. Treat it as a guard against overfitting to the development set, not
as a sample of real meetings.

## 3. Topic linking

### 3.1 Development set (49 cases)

| | `classic` | `llm` | `hybrid` | `hybrid`, floor `0` |
| --- | --- | --- | --- | --- |
| Accuracy | 45/49 = 0.92 | 46/49 = 0.94 | 46/49 = 0.94 | 46/49 = 0.94 |
| Precision of asserted links | 38/42 = 0.90 | 35/35 = 1.00 | 34/34 = 1.00 | 35/35 = 1.00 |
| Recall of asserted links | 38/38 = 1.00 | 35/38 = 0.92 | 34/38 = 0.89 | 35/38 = 0.92 |
| Reached the user (asserted or pending) | 38/38 | 35/38 | 34/38 | **38/38** |
| No-link meetings given an asserted link | 4 of 19 | 0 | 0 | 0 |
| No-link meetings shown a pending link | 15 of 19 | 0 | 15 | 19 |
| LLM calls · input tokens | 0 | 93 · 19,578 | 53 · 11,306 | 53 · 11,306 |

### 3.2 Held-out v3 (100 cases)

| | `classic` | `hybrid` | `llm` |
| --- | --- | --- | --- |
| Accuracy | 90/100 = 0.90 (CI 0.83–0.94) | 93/100 = 0.93 (CI 0.86–0.97) | **96/100 = 0.96** (CI 0.90–0.98) |
| Precision of asserted links | 77/83 = 0.93 | 74/74 = **1.00** | 77/77 = **1.00** |
| Recall of asserted links | 77/81 = 0.95 | 74/81 = 0.91 | 77/81 = 0.95 |
| Reached the user | 81/81 | 80/81 | 77/81 |
| No-link meetings given an asserted link | 2 of 42 | **0** | **0** |
| LLM calls · input tokens · request time | 0 | 104 · 20,863 · 234 s | 315 · 61,986 · 1,014 s |

By category (`classic` → `hybrid` → `llm`): `follow_up` 10/10 → 10/10 → 10/10,
`paraphrase` 10/10 → 10/10 → 10/10, `recurring` 5/6 → 5/6 → **6/6**, `distractor`
8/10 → 7/10 → 8/10, `long_meeting` 12/16 → 13/16 → **14/16**, `large_history` 8/10
→ **10/10** → **10/10**, `unrelated` 10/10 → 10/10 → 10/10, `shared_keyword` 13/14 →
14/14 → 14/14, `same_domain` 14/14 → 14/14 → 14/14.

- `hybrid` against `classic`: six cases fixed (all false links), three broken (a
  true link vetoed or demoted). p = 0.51.
- `llm` against `classic`: nine fixed, three broken. p = 0.15.
- `llm` against `hybrid`: four fixed, one broken. p = 0.38.

Three of the four cases `llm` fixed over `hybrid` are links `classic` left
`pending` and the LLM asserted. That is structural: `hybrid` can only veto what
classic asserts, so it can never promote a link classic was unsure of. `large_history`
was 10/10 for both: `hybrid` is not limited by the five-candidate cap, and the
cap did not cost `llm` a single case with six to nine past meetings.

### 3.3 What it says

- **The LLM is the more precise judge, and on held-out data not the less
  complete one.** On the development set it was both more precise and less
  complete (recall 0.92 against 1.00); on v3 its recall equals `classic`'s (0.95)
  while precision is 1.00 against 0.93. Four true links were missed by `llm`: the
  same count as `classic`, and mostly different ones (one is shared).
- **A missed link is invisible with `llm_pending_floor=0.25`.** `llm` shows no
  pending link at all and "reached the user" is 77/81, below `classic`'s 81/81. A
  veto can demote instead of drop: with the floor at `0`, `hybrid` was the only
  variant on the development set that lost no true link and asserted no wrong one,
  at the price of four more pending questions on no-link meetings. **Not measured
  on v3** (section 6).
- **`pending` noise is `classic`'s own.** It already shows a pending link on 15
  of 19 development meetings that link to nothing; `hybrid` does not add to that
  unless the floor is `0`.
- The mis-linking this module is most exposed to (`../context.md`, "Retrieve
  broad, re-rank narrow") is exactly what the LLM cut: two false links on
  held-out v3 for `classic`, none for `hybrid` or `llm`.

## 4. Decision lineage

### 4.1 Development set (50 cases)

| | `classic` | `llm` |
| --- | --- | --- |
| Accuracy | 49/50 = 0.98 | 49/50 = 0.98 |
| Threading (right thread, or a new one) | 50/50 | 50/50 |
| Change type, among correctly threaded non-new | 39/40 | 39/40 |
| LLM calls · input tokens · request time | 0 | 64 · 14,025 · 187 s |

The single miss differs: `classic` read one replacement as `modified`, the LLM
read a changed owner as `reversed`. The set is saturated and cannot tell the two
apart.

### 4.2 Held-out v3 (100 cases)

| Category | `classic` | `llm` |
| --- | --- | --- |
| `unchanged_restated` | 8/8 | 8/8 |
| `unchanged_paraphrase` | 10/10 | 10/10 |
| `modified_param` | 11/12 | 10/12 |
| `modified_scope` | 9/10 | **5/10** |
| `reversed_cancel` | 10/10 | 10/10 |
| `reversed_alternative` | **0/10** | **10/10** |
| `new_unrelated` | 8/8 | 8/8 |
| `new_same_domain` | 9/10 | 10/10 |
| `distractor` | 8/12 | 11/12 |
| `chain` | 8/10 | 8/10 |
| **Total** | **81/100 = 0.81** (CI 0.72–0.87) | **90/100 = 0.90** (CI 0.83–0.94) |

| | `classic` | `llm` |
| --- | --- | --- |
| Threading (right thread, or a new one) | 98/100 | **100/100** |
| Change type among correctly threaded non-new cases | 64/81 = 0.79 | 72/82 = 0.88 |
| LLM calls · input tokens · request time | 0 | 170 · 36,173 · 471 s |

Confusion matrices (rows expected, columns actual):

| `classic` | unchanged | modified | reversed | new |
| --- | --- | --- | --- | --- |
| unchanged | 24 | 1 | 0 | 0 |
| modified | 2 | 27 | 1 | 0 |
| reversed | 1 | **12** | 14 | 0 |
| new | 0 | 1 | 0 | 17 |

| `llm` | unchanged | modified | reversed | new |
| --- | --- | --- | --- | --- |
| unchanged | 24 | 0 | 1 | 0 |
| modified | 0 | 21 | **9** | 0 |
| reversed | 0 | 0 | 27 | 0 |
| new | 0 | 0 | 0 | 18 |

`llm` against `classic`: eighteen cases fixed, nine broken (p = 0.12). One case is
wrong in both.

### 4.3 What it says

The two modes make opposite mistakes.

**`classic` never calls a cue-less replacement `reversed`.** `pipeline/change.py`
tells a *withdrawn* decision from a *moved parameter* with negation and
cancellation vocabulary (`않`, `취소`, `철회`, `대신`, ...), because NLI scores both
as a contradiction at about the same strength. A replacement that carries none of
those words — "PostgreSQL로 옮기기로 했다", "전면 온라인으로 전환한다", "피그마로
통일한다" — has no cue to fire. The development set's replacements nearly all
carried one: ten of the eleven in the earlier sets (`대신`, `없이`, `말고`, `아닌`,
`아니라`, `접고`), and the eleventh — "스케치에서 피그마로 전환" — is the one
`classic` missed on the development set. So the 0.98 measured how well the cues
cover the phrasings the cues were written for, and the held-out set measured the
rest: twelve of its thirteen `reversed` misses were read as `modified`.

**The LLM calls too many things `reversed`.** All 27 `reversed` are right, and nine
`modified` are not: both changed owners ("리팩토링 담당은 강민구로 바꾸기로 했다"),
five scope changes (security training widened to partner staff, a beta opened to
all customers, a night allowance narrowed to regular staff, code review widened to
every module, parking support narrowed), a translation scope widened to Japanese,
and an API limit lowered from 120 to 90. A tenth case, a price "kept" after a rise,
was read `reversed` instead of `unchanged`. The same pattern appeared once on the
development set (a changed owner read `reversed`). It is not for want of an
instruction: `judge-v1` lists owner and scope under `modified` and defines
`reversed` as "withdrawn, cancelled or replaced by its opposite". The lightest
model tier reads a statement that excludes part of the old one as the opposite of
it anyway. Whether a stronger model or a reworded prompt fixes that is a question
for a new held-out set — **not** for `*_heldout_v3`.

**What this does and does not allow.** The two error profiles are complementary
(`classic` under-calls `reversed`, the LLM over-calls it), and that suggests asking
the LLM only where classic's cues are silent, or letting classic's cues overrule an
unsupported `reversed`. That is a hypothesis, and this set has now been read to
form it. It needs a new held-out set before it is a result.

## 5. Cost and operational notes

- **Calls.** Development set: `llm` 93 calls on topic linking and 64 on lineage;
  `hybrid` 53. Held-out v3: `hybrid` 104 on topic linking; `llm` 315 on topic
  linking and 170 on lineage. No unusable answers in any completed run.
- **Money.** All of it ran on the Gemini free tier, so nothing was spent. At the
  token counts above, a full `llm` run of both held-out suites is about 98,000
  input and 9,600 output tokens (61,986 + 36,173 in; 6,294 + 3,295 out).
- **Latency.** About 2 to 3 seconds of request time per verdict, run one at a
  time to stay under a per-minute limit. `llm` on a held-out suite took 8 minutes
  (lineage) to 17 minutes (topic linking). That is fine for evaluation and the
  reason to prefer `hybrid` (104 calls against 315 on the same suite) for anything
  on the live path.
- **Free-tier limits are per model and small.** `gemini-3.8-flash`, `3.5-flash`
  and `flash-latest`: 20 requests a day. `3.6-flash` and `3.7-flash`: 5 a
  minute. `*-flash-lite`: 15 a minute, and `3.5-flash-lite` 500 a day: the two
  `llm` runs of 2026-10-01 used 485 of them and the third run that day (`hybrid`,
  floor `0`) was refused. The daily limit of the other flash-lite models was not probed. The
  2.5 models answer 404 to new users. The quota message is hidden by `HttpClient`
  (status only); read it with `curl`.
- **Two real bugs found by running it:** the judge ignored `llm_max_tokens`
  (hard-coded 2048, which a reasoning model's thinking eats), fixed here; and
  `KlueKorNliLocal` crashed on transformers 5.x, which returns the per-label
  scores one level shallower than 4.x. The second was fixed on `main` in the
  meantime (#395). The runs here were made with a local fix of the same thing; the
  development set and v5 (cue on) were re-run on `main`'s version and gave the
  same numbers (50/50 and 98/120). The other runs were not repeated.

## 6. Not measured, and what would settle it

| Open question | Status |
| --- | --- |
| Does `llm` fix the `reversed_alternative` gap on held-out v3? | **Answered: yes** — 10/10 against `classic`'s 0/10 (section 4) |
| Pure `llm` on held-out topic linking | **Answered**: 0.96 (section 3.2) |
| `hybrid` with `llm_pending_floor=0` on held-out v3 | **Not run**: the daily quota ran out. About 100 calls |
| Is the LLM's lead real? | Neither lead is significant at 100 cases (p = 0.15 and 0.12). It needs more cases or a replicate on a new set |
| Does fixing `judge-v1`'s tendency to say `reversed` cost anything else? | A new prompt version, measured on a **new** held-out set |
| Does combining the two modes beat either alone? | The same new set |
| A stronger model than the lightest tier | the same runs on a paid tier |
| Run-to-run noise of one model | repeat any run three times and report the spread |

```bash
export AUTUNE_CONTEXT_EMBEDDER_IMPL=kure_v1_local \
       AUTUNE_CONTEXT_RERANKER_IMPL=bge_reranker_v2_m3_ko_local \
       AUTUNE_CONTEXT_NLI_IMPL=klue_kornli_local \
       AUTUNE_CONTEXT_NLI_LOCAL_MODEL=<path to the KorNLI checkpoint> \
       AUTUNE_CONTEXT_LLM_IMPL=gemini AUTUNE_CONTEXT_LLM_MODEL=gemini-3.5-flash-lite \
       AUTUNE_CONTEXT_LLM_API_KEY=<key> AUTUNE_CONTEXT_LLM_CONCURRENCY=1
uv run --package autune-context python -m autune_context.eval decision-lineage \
    --dataset decision_lineage_heldout_v3.json --mode llm
```

Use a scratch database, not the shared one: the harness refuses a database that
holds real meetings, and a database migrated from another branch will not
migrate here.

## 7. Where this leaves the decision

- **`classic` stays the default for now.** The LLM is ahead on both held-out
  suites, but not by a margin 100 cases can establish, and it has an error of its
  own. Nothing here is a reason to switch before the other conditions below.
- **On topic linking, `llm` was the best mode on held-out data and `hybrid` the
  cheapest improvement.** `llm` costs about three times the calls of `hybrid`
  (315 against 104) for three more correct cases (96 against 93). Which is worth it
  depends on a latency and cost budget nobody has set; `hybrid` with the floor at
  `0` on this set is the missing point.
- **On decision lineage, `classic` has a defect independent of any LLM** (a
  replacement with no cue word is read as a parameter change) that the held-out set
  is the evidence for. The LLM does not have that defect and has the opposite one.
- **Before any real meeting reaches an external provider** the team has to choose
  one (OpenAI and Gemini are the candidates), including where its endpoint runs;
  see [`../context.md`](../context.md) ("Privacy notes", "Open questions") and
  [`../../engineering/external-approvals.md`](../../engineering/external-approvals.md).
  This evaluation ran only on Gemini; nothing in it says OpenAI would do the same.
- **The next evaluation set** should be masked real meetings, labelled by someone
  other than the author, and large enough to make a lead of this size visible.
  `*_heldout_v3` stays frozen for the comparisons in section 6 and becomes a
  development set the moment anyone tunes against it.

## 8. Addendum (2026-10-01): a replacement cue for `classic`

The comparison above was meant to compare three modes, so `classic` got exactly one
change, aimed at the gap of section 4.3: `marks_replacement` in `pipeline/change.py`.
A *swap verb* (`전환`, `교체`, `대체`, `이관`, `이전`, `통일` + `하다`; `갈아타`, `바꾸`,
`옮기`, `맡기`) whose `(으)로`/`에` target is a **new thing** makes a contradicted or
neutral pair `reversed`. It stays quiet when the target holds a number, a weekday or
time of day, or a unit ("월 단위로"), because a parameter is moving; and when the
decision names an owner (`담당`, `책임`, `주관`, `리드`, `오너`), because a replaced
owner is a moved parameter by the labelling policy. No other rule changed, and
entailment still takes priority over it.

### 8.1 Before and after (`classic` lineage, accuracy)

| Set | before | after |
| --- | --- | --- |
| Development set `*_v2` (50) | 49/50 = 0.98 | **50/50 = 1.00** |
| Held-out `*_heldout_v1` (30) | 29/30 | 29/30 |
| Held-out `*_heldout_v2` (26) | 22/26 | 22/26 |
| Cue probes v1 (6) | 5/6 | 5/6 |
| Cue probes v2 (24, new) | — | 19/24 = 0.79 (the function alone: 24/24) |
| Held-out `*_heldout_v3` (100), **not held-out for this rule** | 81/100 = 0.81 | 91/100 = 0.91 |

On v3: `reversed_alternative` 0/10 → 9/10, `reversed` misses read as `modified`
12 → 2, change type among correctly threaded non-new cases 64/81 → 74/81. The
modified cases the guards were written for did not turn into `reversed`: modified
read as `reversed` stays at one (the `대신` owner case, an old rule), 1 → 1. No
regression on any earlier set, and the development set's one miss is fixed. No API
call was made.

### 8.2 What the 0.91 is not

- **It is not a held-out result.** The rule was written *because* of what v3 showed,
  and the owner, weekday and numeric guards were written with v3's `modified` cases in
  view. v3 stays a fair baseline for `classic` as it was (0.81); it is no longer a fair
  test of `classic` with this rule. A new held-out set is needed for that — and section 9 is that set.
- **It is not `classic` catching up with the LLM.** The LLM's 0.90 was measured on a
  set it had not been shaped to; `classic`'s 0.91 was not.
- **The probes are close to tests.** Cue probes v2 were written after the rule, and
  its twelve negatives mirror the guards. They show the rule does what it says, and
  nothing about generalisation.

### 8.3 Where the probes still fail, and why it is not the rule

Five of the 24 probes fail end to end although the rule alone gets all 24:

| Probe | What happened |
| --- | --- |
| `cp2_inquiry_bot` | Not threaded: head similarity 0.642, under `lineage_match_threshold` (0.65) — a new thread, so the cue is never asked |
| `cp2_mobile_stack`, `cp2_payroll` | NLI read the pair as entailed, so `unchanged` won before the cue was consulted |
| `cp2_owner_jugwan`, `cp2_lunch_time` | The rule correctly stayed quiet; NLI read the pair as entailed, so `unchanged` instead of `modified` |

So the swap cue is bounded by the threading threshold and by NLI's entailment
reading, both upstream of it. Letting the cue overrule entailment was considered and
not done: it would also rewrite genuine restatements that happen to name a new word.

Two limits came from reading v3 and were **left alone on purpose**, since fixing them
against that set would spend it further. A target containing a digit ("3PL 업체에
맡기기로") reads as a quantity and the cue stays quiet; and a return to something that
was already in the earlier statement ("다시 A사로 되돌린다") is not "new", so it is not
caught. Both belong to the next iteration, measured on a new set.

### 8.4 Reproduce

```bash
uv run --package autune-context python -m autune_context.eval decision-lineage \
    --dataset decision_lineage_cue_probes_v2.json --mode classic
```

Unit tests: `tests/unit/test_lineage.py` (the replacement cue, its guards, its known
limit, and how `classify_change` uses it).

## 9. Held-out v4 (2026-10-01): `classic` with and without the replacement cue

Section 8 left a question it could not answer: how much of the 0.81 → 0.91 on v3 is
the rule, and how much is that the rule was written from v3's errors? `*_heldout_v4`
(120 lineage cases) was written to answer it, and only `classic` was measured: the
LLM and `hybrid` were not run on it, so nothing here compares them to the LLM numbers
of sections 3 and 4.

### 9.1 The set

| | |
| --- | --- |
| **Cases** | 120: `unchanged_restated` 10, `unchanged_paraphrase` 10, `modified_param` 14, `modified_scope` 10, `reversed_cancel` 12, `reversed_alternative` 20, `new_unrelated` 8, `new_same_domain` 10, `distractor` 14, `chain` 12 |
| **Written** | After the cue existed, by its author, and **frozen before the cue or any model was run on it** — the build script never calls `marks_replacement`. Not independent: the same author, no second labeller |
| **Kept from being shaped to the rule** | Mixed register (48% of current statements end in a spoken form such as -요 / -죠), because B's decision statements are often a last-utterance quote and v3 was all written style. `reversed_alternative`: 8 of 20 statements use a swap verb the cue knows and 12 do not (도입, 말고, 대신, -로 가요, -로 정했어요, 쓰기로, 돌린다). Parameter changes use the same verbs a team really uses (옮기다, 바꾸다, 교체) at natural frequency. Cases whose label could be argued both ways (a state toggle such as free → paid) were not written |
| **Overlap** | None with any earlier set: a script refused any statement already used |

The cue was switched off for the baseline by replacing `marks_replacement` with an
always-false function at run time, so both runs are the same code on the same cases.

### 9.2 Result

| | cue off | cue on |
| --- | --- | --- |
| Accuracy | 86/120 = 0.72 (CI 0.63–0.79) | **91/120 = 0.76** (CI 0.67–0.83) |
| Threading (right thread, or a new one) | 112/120 = 0.93 | 112/120 = 0.93 |
| Change type among correctly threaded non-new | 70/96 = 0.73 | 75/96 = 0.78 |

The cue fixed six cases and broke one (sign test, p = 0.125): not established at this
size, but the direction is the one intended. By category:

| Category | cue off | cue on |
| --- | --- | --- |
| `unchanged_restated` | 9/10 | 9/10 |
| `unchanged_paraphrase` | 9/10 | 9/10 |
| `modified_param` | 13/14 | 12/14 |
| `modified_scope` | 10/10 | 10/10 |
| `reversed_cancel` | 9/12 | 9/12 |
| `reversed_alternative` | 3/20 | **8/20** |
| `new_unrelated` | 8/8 | 8/8 |
| `new_same_domain` | 8/10 | 8/10 |
| `distractor` | 8/14 | 9/14 |
| `chain` | 9/12 | 9/12 |

Confusion, rows expected, columns actual (unchanged / modified / reversed / new):
`reversed` went from 3 / 19 / 14 / 5 to 3 / 13 / **20** / 5, and `modified` from
2 / 31 / 0 / 1 to 2 / 30 / **1** / 1 — the one false `reversed`.

### 9.3 What it says

- **The rule is worth +5 here, against +10 on v3.** v3 is the set the rule was
  written from; v4 is not, and has a different mix, so the gap is not all optimism —
  but the optimism is in that direction. On v4, `reversed_alternative` goes from 15%
  to 40%, and that is nearly all of what changes.
- **A verb list covers a minority of real phrasing.** Of the eight swap statements that
  use a verb the cue knows, five are fixed, and the other three are not threaded at all
  (head similarity under the 0.65 threshold, so the cue is never asked). Of the nine that
  use none of the cue's verbs and no cue word, **none** is fixed — "문서는 이제 워드로
  작성하는 걸로 가요", "인사 시스템은 상용 솔루션을 도입하기로 했다", "배송 업체를
  한진으로 정했어요", "근태는 앱으로 자동 기록하는 걸로 해요". People mostly do not say
  *swap*; they say what they chose. A longer verb list would catch a few more, and
  would also catch more parameters. This is the ceiling of a lexical cue, and the
  reason the remaining gap needs a judgement of meaning (the LLM, or a classifier
  trained for it).
- **The one case it broke is a gap in the guard, not in the idea.** "릴리스 책임자를
  서도현으로 교체한다" (an owner, so `modified`) read `reversed`: kiwipiepy makes
  `책임자` and `담당자` single tokens, and the owner guard matches `책임` and `담당`
  whole. The most ordinary way of writing an owner change is the one it misses.
  Unfixed on purpose — fixing it against this set spends the set.
- **Most of what `classic` still gets wrong is not about replacements.** Threading
  fails on 8 of 120 (six true threads missed, two false ones made between different
  decisions about the same subject), and NLI reads some pairs as entailed ahead of every
  cue: "시범 운영은 중단해요" `unchanged`, a modification that adds a second approver
  `unchanged`.
- **`classic` was harder here than on v3 before the cue** (0.72 against 0.81), with a
  different mix of cases and a spoken register. The two sets are not the same
  difficulty, so v4 is not a re-measurement of v3.

### 9.4 Where this leaves `classic`

`classic` with the replacement cue scores **0.76 on a held-out set it was not written
from**, against 0.72 without it. The 0.98 on the development set, the 0.81 and 0.91 on
v3 are all numbers the rules had seen; 0.72 and 0.76 are the first it had not.

v4 has now been read to write this section. It stays frozen for as long as nothing is
tuned against it, and any fix for what it showed — the `책임자` guard, the threading
threshold, the phrasing the verb list misses — needs a **new** set before its effect is
claimed. It also does not say how the LLM would do on it: the LLM's 0.90 on v3 and
`classic`'s 0.76 on v4 are different sets and cannot be set against each other.

### 9.5 Reproduce

```bash
# cue on
uv run --package autune-context python -m autune_context.eval decision-lineage \
    --dataset decision_lineage_heldout_v4.json --mode classic
```

For the cue-off baseline, run the same command after replacing
`autune_context.pipeline.change.marks_replacement` with `lambda *a, **k: False` in the
same process (the cue is looked up at call time).

## 10. Held-out v5 (2026-10-01): after the guard fixes

v4 showed the owner guard missing `책임자` and `담당자`; v3 had shown a digit inside a
name ("3PL") being read as a quantity. Those two guard gaps were fixed, and nothing
else in the rule — the verb list, the threading threshold and entailment's priority
are as they were. `*_heldout_v5` (120 cases) measures the result on sentences none of
this was written from.

### 10.1 What changed in the rule

- **Owner guard.** It now knows the agent nouns `담당자`, `책임자`, `주관자` and `리더`
  as well as `담당`, `책임`, `주관`, `리드`, `오너`: kiwipiepy makes each of them a single
  token.
- **Digit inside a name.** A digit run is part of a name, not a quantity, when Latin
  letters come *before* it (`S3`, `B2B`, `K8s`) or when letters that are not a unit
  come after it (`3PL`, `5G`). Letters that are a unit (`10GB`, `200ms`, `5K`) keep it a
  quantity. A unit follows its number, so a letter before one is never a unit; a first
  version of this fix did not know that and would have read `S3` as a quantity.

### 10.2 The set

`*_heldout_v5` follows v4's design with new subjects (clinics, schools, shops, hotels,
labs, games): 120 cases in the same ten categories, 50% of current statements in a
spoken register, 9 of 20 `reversed_alternative` statements using a swap verb the cue
knows. **Written and frozen before the cue or any model was run on it**, by the
author of the cue and knowing the two fixes — so owner changes appear at their natural
frequency (3 of 14 `modified_param`) and one statement contains a digit-bearing name.
Not independent. Measured three ways on the same code and cases: cue off, the cue
before the guard fixes (a snapshot of `change.py`), and the cue after.

### 10.3 Result

| | cue off | cue before the guard fixes | cue after |
| --- | --- | --- | --- |
| Accuracy | 89/120 = 0.74 (CI 0.66–0.81) | 95/120 = 0.79 (CI 0.71–0.85) | **98/120 = 0.82** (CI 0.74–0.88) |
| Threading | 113/120 | 113/120 | 113/120 |
| Change type among correctly threaded non-new | 74/98 = 0.76 | 80/98 = 0.82 | 83/98 = 0.85 |

| Comparison | fixed | broken | sign test |
| --- | --- | --- | --- |
| cue off → cue after | 10 | 1 | **p = 0.012** |
| cue off → cue before the fixes | 9 | 3 | p = 0.146 |
| cue before the fixes → cue after | 3 | 0 | p = 0.250 |

By category (off → after): `reversed_alternative` 3/20 → **12/20**; `modified_param`
10/14 → 10/14 (8/14 before the guard fixes: the two owner cases it broke); `distractor`
11/14 → 11/14; every other category unchanged. Confusion (rows expected, columns actual;
unchanged / modified / reversed / new): `reversed` 3 / 18 / 18 / 1 → 3 / 8 / **28** / 1.

### 10.4 What it says

- **On a fresh set the cue is worth +9 cases net, and it is significant** (p = 0.012;
  on v4, under the old guards, it was +5 and p = 0.125). The same direction on two
  sets the rule was not written from.
- **The guard fixes did what they were for and cost nothing here.** The `담당자` and
  `책임자` owner changes and the `K8s` swap are right, and nothing else moved. Three
  cases is too few to call it a significant improvement on its own (p = 0.25).
- **The ceiling is the same as on v4.** All nine swaps said with a verb the cue knows
  are right — `전환`, `대체`, `이관`, `교체`, `갈아타`, `옮기`, `통일`, `맡기`, `바꾸`. Of the
  eleven said another way, the three that carry a cue word (`말고`, `대신`) are right and
  the eight that carry none are not ("정산 시스템은 외부 솔루션을 도입하기로 했어요", "회계는
  세무법인에 위탁하는 걸로 정했어요", "배달은 제휴 라이더로 돌린다"). On v4 it was 0 of 9.
  A verb list does not reach what people say when they say what they chose.
- **One new false positive, and it is a common construction.** "재고 실사를 반기마다
  하는 걸로 바꿔요" (a moved frequency, `modified`) read `reversed`: kiwipiepy tags `걸`
  (것 + 으로) as a noun, so the cue saw a new thing. "…하는 걸로 바꿔요 / 가요" is a very
  ordinary way to say a decision aloud. It is the next guard gap, and it is **not**
  fixed: this set has been read to find it.
- **One false `reversed` is older than the cue.** "예약 취소 무료 기한을 5일 전으로
  늘려요" reads `reversed` because of the cancellation noun `취소`, which
  `marks_reversal` has always treated as a cue.
- **The rest of the misses are upstream of the cue:** threading fails on 7 of 120, and
  NLI reads some pairs as entailed ahead of any cue ("장학금을 학기당 250만 원으로
  올린다" `unchanged`).

### 10.5 Where this leaves `classic`

`classic` with the replacement cue scores **0.76 on v4 (before the guard fixes) and 0.82
on v5 (after)**, against 0.72 and 0.74 without it. The two sets are not the same
difficulty, so 0.76 → 0.82 is not the guard fixes' doing; the paired comparison on v5
(3 fixed, 0 broken) is. v4 was read to find the `책임자` gap, so it is no longer a
clean test of the rule as fixed; v5 is, and it has now been read too. The `걸로` gap,
the verb-list ceiling and the threading threshold need a **new** set (v6) before any
claim about a fix. No threshold or verb was chosen from either set.

### 10.6 Reproduce

```bash
uv run --package autune-context python -m autune_context.eval decision-lineage \
    --dataset decision_lineage_heldout_v5.json --mode classic
```

For the cue-off and cue-before-the-fixes columns, replace
`autune_context.pipeline.change.marks_replacement` in the same process (with
`lambda *a, **k: False`, or with the function from the earlier `change.py`).
