# Evaluation 04 — how a terse decision changed

Date: 2026-10-13. Mode: `classic`, local models (section 6 of
[`01-engine-mode-comparison.md`](01-engine-mode-comparison.md)), scratch databases.

Fix 3 ([`03-realistic-heldout-sets.md`](03-realistic-heldout-sets.md), section 9) made
decision lineage read a decision's source utterances, so a terse decision ("없던 걸로
한다", "그대로 두기로 했다") finds its thread. Its change type was still classified on
the statement, and NLI cannot compare "지금대로 둔다" with "매장 영업은 밤 10시까지
한다": the two share nothing. On held-out v6, `terse_unchanged` was 2/5 and
`terse_reversed` 2/5 after fix 3. This document covers fix 4, which classifies a terse
decision on the line it was settled in, and the two sets written to build and measure it.

## 1. The sets

| File | Role | Cases | Frozen as (`git hash-object`) |
| --- | --- | --- | --- |
| `decision_lineage/fixtures/decision_lineage_heldout_v7.json` | held-out | 60 | `c6f173e157a2bc1e41e24b5a920e03fae9797d9f` |
| `decision_lineage/fixtures/decision_lineage_terse_dev_v1.json` | development | 42 | — |

**Held-out v7** was written and frozen before fix 4 was designed and before anything ran
on it. 42 of its 60 cases are terse: 12 unchanged, 12 modified, 12 reversed, and 6
`terse_new`, whose terse statement must not join the unrelated past decision it is shown
with. The other 18 restate, modify or reverse in full sentences, to catch what the fix
breaks. 15 cases add a second, unrelated past decision of the same team. Terse statements
are phrased as a team says them, at natural frequency. That includes a keep said with a
negation (바꾸지 않기로 했다) and a withdrawal said without one (엎기로 했다). The subjects
are a hospital ward, a farming cooperative, a game studio, a hotel, a logistics warehouse,
a university lab and an apartment management office.

v7 was frozen twice. The first freeze, `faafcfade2b6f4cd7cd77b32afa085bf47b3266b`, said
"48 of 60" terse cases in its `_about`; the count is 42. Only that sentence was corrected,
before anything had run on the set. The cases are byte-identical.

**The development set** was written after v7 was frozen, with other subjects: a cafe, a
dental clinic, a translation agency, a campsite, an online shop and a recycling centre.
It has 10 terse cases each of unchanged, modified and reversed, 6 `terse_new`, and 6
full-statement controls. Fix 4 was designed on it and on fix 3's development set
`decision_lineage_context_dev_v1.json`.

Neither set repeats a statement or a context line from an earlier set, and neither takes
anything from the development meetings. A script checked both.
The names in them are made up. Both sets were written by the engine's author, so they are
not independent; the caveats in section 4 of `03-realistic-heldout-sets.md` apply.

## 2. What the development data showed

Before the fix, on the terse development set, accuracy was 23/42. `terse_unchanged` was
0/10: six read as `modified`, because NLI is neutral between a terse keep and a full
statement, and neutral meant a moved parameter. Two read as `reversed`, because "고치지
않기로 했다" carries a negation. Seven terse decisions did not thread at all: their joined
utterances were diluted by a complaint or a reason.

Change type was tried on several texts, with correct threading assumed (58 non-new
cases across both development sets):

| Text classified for the later version | Correct |
| --- | --- |
| The statement (as before) | 37/58 |
| The source utterance closest to the earlier statement | 39/58 |
| The last source utterance | 40/58 |
| The statement plus either of those | 34/58 |
| **The closest utterance only when it is closer than the statement** | **46/58** |

The closest or last utterance alone fixed terse keeps but broke every full-statement
`unchanged` case. The utterance said the same thing in looser words, and NLI no longer
entailed it. Gating on closeness gave both. In both development sets, every full
statement was closer to the earlier statement than any of its utterances was, and every
terse statement was further away than its best utterance.

## 3. What changed

Three parts. Each one reads only consented speech, at the time it runs, through the same
query (`service._consented_texts`).

1. **Threading reads one line at a time** (`service._line_vectors`,
   `_assign_decisions_to_threads`). A (decision, head) pair now scores the highest of
   three cosines: statement to statement; the two sides joined with their utterances
   (fix 3); and the decision's single utterance closest to the head's statement. The
   threshold is still `lineage_match_threshold` (0.65). On the development sets, no
   unrelated decision reached it. The highest wrong score was 0.62, the same as under
   fix 3.
2. **Change type reads the line that says it** (`service._change_hypotheses`, called
   from `_rethread`). The later side of each adjacent pair is the version's statement,
   unless one of its source utterances is closer to the earlier statement than the
   statement is. Then that utterance is classified, by NLI and by the cues alike.
   `current_statement` and `previous_statement` still hold the statements. `llm` mode is
   unchanged.
3. **A keep that NLI cannot place is a keep** (`pipeline.change.marks_keep`). Where NLI
   neither entails nor contradicts, a statement that says nothing changes reads
   `unchanged` instead of `modified`. Such a statement contains a keep adverb (그대로,
   계속), a keep noun (유지, 동결), "as now or as before" (지금대로, 기존처럼), "no change"
   (변동 없이), or a change verb said with a negation (바꾸지 않는다, 안 고친다). A
   negated change verb also no longer counts as a withdrawal in `marks_reversal`.
   Where NLI contradicts, the cue does not speak, so "A사를 계속 쓰기로" after a switch
   away from A is not rescued.

The vocabulary is general Korean for keeping and changing, not words lifted from the
sets. Some of it was added after a failure on the development set showed it was missing.

## 4. Results

Old code against new, one process, same cases. A = before fix 4, B = part 1,
C = parts 1 and 2, D = all three (the shipped code).

**Development sets** (per-case lines read):

| Set | A | B | C | D | A → D fixed / broken |
| --- | --- | --- | --- | --- | --- |
| `decision_lineage_terse_dev_v1.json` (42) | 0.548 | 0.619 | 0.810 | **0.857** | 13 / 0, p = 0.0002 |
| `decision_lineage_context_dev_v1.json` (30) | 0.800 | 0.800 | 0.833 | **0.867** | 3 / 1 |

The case that broke is a new owner ("승아 코치님이 이어서 맡아 주세요"). NLI calls that
utterance entailed by the earlier owner's statement.

**Earlier sets, statements only** (A and D): `decision_lineage_v2` 1.000, held-out v1
0.967, v2 0.846, v3 0.920, v4 0.767, v5 0.817. Every one is identical before and after,
with 0 fixed and 0 broken. These sets have no source utterances, so only part 3 could
reach them, and it changed nothing.

**Held-out v7** (one run, the final code, aggregates only; per-case lines not read):

| | A | B | C | D |
| --- | --- | --- | --- | --- |
| Accuracy (60) | 36/60 = 0.600 | 0.633 | 0.683 | **45/60 = 0.750** |
| Fixed / broken against A | | 2 / 0 | 7 / 2 | **11 / 2, sign test p = 0.022** |
| Threading | 0.917 | 0.967 | 0.967 | 0.967 |
| Change type among correctly threaded non-new | 0.612 | 0.615 | 0.673 | 0.750 |
| `terse_unchanged` | 0/12 | 0/12 | 5/12 | **9/12** |
| `terse_modified` | 12/12 | 12/12 | 11/12 | 11/12 |
| `terse_reversed` | 5/12 | 7/12 | 6/12 | 6/12 |
| `terse_new` (must stay new) | 6/6 | 6/6 | 6/6 | 6/6 |
| `unchanged` / `modified` / `reversed` | 4/6 · 5/6 · 4/6 | the same | the same | the same |

**Held-out v6** (its second run, aggregates only). Fix 3 had used it once, and section 9
of `03-realistic-heldout-sets.md` kept it held-out for a change-type fix:

| | A | B | C | D |
| --- | --- | --- | --- | --- |
| Accuracy (60) | 48/60 = 0.800 | 0.817 | 0.867 | **54/60 = 0.900** |
| Fixed / broken against A | | 1 / 0 | 6 / 2 | **7 / 1, p = 0.070** |
| Threading | 0.967 | 0.983 | 0.983 | 0.983 |
| `terse_unchanged` / `terse_modified` / `terse_reversed` | 2/5 · 5/6 · 2/5 | 2/5 · 5/6 · 3/5 | 3/5 · 6/6 · 5/5 | **5/5 · 6/6 · 5/5** |
| `terse_new` | 6/6 | 6/6 | 6/6 | 6/6 |
| `reversed` (full statements) | 6/8 | 6/8 | 5/8 | 5/8 |
| other full-statement categories | 27/30 | the same | the same | the same |

Pooled over the two held-out sets: **18 fixed, 3 broken, sign test p = 0.0015**.

- **Established on held-out data.** A terse keep is now read as a keep: 0/12 → 9/12 on
  v7 and 2/5 → 5/5 on v6. Fix 3 had left most terse keeps as `modified`, which falsely
  told a team that a decision had moved. No `terse_new` case joined a thread on either
  set, so the line-level threading added no false thread there.
- **What it costs.** Of the three broken cases, the category counts place one in v7's
  `terse_modified` and one in v7's `terse_reversed`, both appearing at part 2. The third
  is in v6's full-statement `reversed`, also at part 2. In that case an utterance was
  closer to the earlier statement than the statement was, though the statement named its
  subject. The per-case lines have not been read, so which cases these are, and why, is
  not known. Reading them would make v6 and v7 development data for any later fix of the
  same rule.
- **Left.** On the development sets, a withdrawal said without a cue word is still read
  as `modified`. Examples are "배달 앱에서는 빠지죠", "계약을 끊고 …에 넘기죠", "다시
  카운터에서 주문 받죠" and "사람이 처음부터 하기로 했다". v7's `terse_reversed`, at 6/12,
  says the same. These are the replacement and cancellation cues' known limits
  (`pipeline.change`), not something reading the context can fix.

## 5. What the sets are worth now

- v6 has been run twice, aggregates only, for fixes 3 and 4. It no longer counts as
  held-out for decision lineage.
- v7 has been run once, aggregates only. It still counts as held-out for a fix that does
  not tune the three parts above, for example a new cancellation cue. Under the rules in
  section 5 of `03-realistic-heldout-sets.md`, the next run must again put old and new
  code side by side.
- The terse development set is development data. Its per-case lines have been read.

```bash
uv run --package autune-context python -m autune_context.eval decision-lineage \
    --dataset decision_lineage_heldout_v7.json --mode classic
```
