# Evaluation 03 — held-out sets for the next round of fixes

**Written:** 2026-10-12 · **Owner:** 문민재 ·
**Module:** Meeting Context Engine (`autune_context`) · **Status:** sets frozen; fixes 1
and 2 measured on v4 (sections 7 and 8), fix 3 on v6 (section 9); per-case results unread

Two demo meetings, run through the engine in `classic` mode, showed five weaknesses
that the existing evaluation sets do not catch. Both were played from a made-up demo
script, not a real conversation. The existing sets are short, synthetic and mostly one
topic per meeting. This document describes two new held-out sets written to measure
fixes for those weaknesses. It also covers the harness changes they need and the rules
for using them. Results go here as each fix is measured.

## 1. What the two meetings showed

A team's first and second meeting on the same project were run through module A's
masking, persistence and payload code (`TranscriptReady`), then through the engine.
Every topic of the second meeting was linked to the first, which is right: it is a
follow-up. But the reasons were weak. Nothing said in those meetings is quoted here, and
no score from them is reported; the examples below are made up to show each weakness.

1. **Labels.** Many segment labels were a person's name, an interjection or a filler
   noun. A label is the first noun phrase of the segment, and in speech that is often a
   name ("<name> 님, …") or a word like "그나마".
2. **Openings and closings.** Each meeting's closing (for example "오늘은 여기까지
   하고 목요일에 뵙겠습니다") became a segment of its own, and the two closings linked
   each other more strongly than any two real topics did.
3. **The re-ranker is near zero on real segments.** Most asserted links had a re-rank
   score close to zero and were asserted on dense similarity alone. The cross-encoder
   answers "does this passage answer this query". A status update ("예약 알림 문자는
   테스트 발송까지 끝났습니다") is not an answer to the plan it follows ("예약 알림 문자를
   다음 주까지 테스트 발송한다"), so the score stays near zero. A question or a short
   topic phrase against the same past passage scores high with the same model.
4. **Permissive similarity.** Two long meetings of one team sit well above
   `link_similarity_threshold` (0.74) on almost any pair of segments. With a single past
   meeting this could not show a false link, but it cannot separate topics either.
5. **Terse decisions do not thread.** A short statement such as "2번 시안으로 정한다"
   does not join the earlier decision it settles, because what "2번 시안" means is in the
   utterances the decision was settled in, not in the statement.

These two meetings were for finding the weaknesses and for testing, not for designing
fixes: a fix is developed on synthetic development data (section 5). Neither held-out set
contains what was said in them or a paraphrase of it (section 4).

## 2. `topic_linking_heldout_v4.json`: series of real-length meetings

A new file format, `version: 2`: a list of **series**, each one team's meetings oldest
first. Every meeting is a run of **blocks**, each tagged with the topic it is about, or
`null` for openings, closings and small talk.

| | |
| --- | --- |
| Series | 6 teams (game studio, clinic, school, online shop, research lab, hotel), 7 meetings each |
| Scored | every meeting from the second on, with all earlier ones as its history: **36 meetings, 126 meeting pairs, 44 expected links** |
| No-link meetings | 12 of 36: in every team the second meeting, and one other, share no topic with any earlier meeting |
| Meetings | 8–18 utterances in a spoken register; two topics each (8 of 42 have one); names of the people present occur in the text |
| Untagged blocks | every meeting opens and closes, almost word for word the same within a team; some have small talk in the middle |
| Cross-topic words | a topic's words appear inside another topic's blocks (a delivery query in a chatbot block, a server in a safety check) |

**What counts as a link is derived, not hand-written.** A meeting should link to exactly
the earlier meetings that share at least one topic tag with it. Accuracy and pair-level
precision and recall are therefore the same measures v1–v3 report. Because the blocks are
tagged, the runner adds two measures:

- **Labels naming their topic.** Each segment takes the tag most of its utterances carry.
  Its label counts as naming that topic if it contains one of the topic's declared words
  (`"topics"` in the file). A segment that is mostly an opening, a closing or small talk
  never counts, and the runner reports how many there are.
- **Asserted links on topic.** An asserted link row counts as on topic if a segment under
  its label is about a topic the linked meeting had. The runner also counts the rows made
  by an opening, closing or small-talk segment.

Problems 1, 2 and 4 show up directly in these measures. Problem 3 shows in the same
numbers when the re-ranker's input changes.

## 3. `decision_lineage_heldout_v6.json`: decisions with their source utterances

Same file format as v1–v5. Each meeting now also has a **`context`**: the two to four
utterances its decision was settled in. The runner stores them as that meeting's
utterances, from one consenting speaker, and the decision cites all of them in
`source_utterance_ids`, as module B does. Today's code ignores them, so a change that
reads them can be measured against the current behaviour on the same cases.

| Category | Cases | What it holds |
| --- | --- | --- |
| `terse_unchanged` / `terse_modified` / `terse_reversed` | 5 / 6 / 5 | the statement leaves out what it is about ("2번 시안으로 정한다", "2주 미루기로 했다", "없던 걸로 한다"); only the context says it |
| `terse_new` | 6 | the same kind of statement, but the context is about something else: reading it must not invent a thread |
| `unchanged` / `modified` / `reversed` | 6 / 8 / 8 | full statements, as in earlier sets |
| `new_same_domain` | 6 | full statements, a different decision in the same area |
| `distractor` | 6 | three past decisions, one right thread (three of the six current statements are terse) |
| `chain` | 4 | the past decisions already form a thread (three of the four current statements are terse) |

60 cases: 12 `unchanged`, 20 `modified`, 15 `reversed`, 13 `new`. Labels follow
`decision_lineage/dataset.py`. The 38 full-statement cases are there to catch what
reading the context breaks.

## 4. How they were written, and what they are worth

- **Written and frozen before anything ran on them**: not `classic`, not `fake`. The
  harness tests use synthetic data of their own. Frozen as these git blobs:

  | File | `git hash-object` |
  | --- | --- |
  | `topic_linking/fixtures/topic_linking_heldout_v4.json` | `687e5c26e19648ebda26a02522704b5aac6d7c32` |
  | `decision_lineage/fixtures/decision_lineage_heldout_v6.json` | `438ea5b11f728a94f03bdae7af52d986c5665618` |

- **No overlap.** A script compared every statement, context line and tagged line with
  every earlier fixture, and with the two demo meetings, using character bigrams.
  It flagged three near-copies of earlier lineage sets, which were rewritten. A first
  draft had one series and ten lineage cases built on the demo meetings; they were
  rewritten in other domains before freezing. In review two more kinds of line were found
  close to the demo meetings: one terse decision in v6 and the closing formula three
  teams of v4 used. Both were reworded and the sets frozen again. v6 had not been run.
  v4 had: at its first freeze (`89ef975975926b0836d382430317d5d8ecf31a4e`) it had been
  run twice, aggregates only, to measure fixes in progress alongside the review. The
  reword touched only closing lines, which are untagged and score nothing themselves.
  The reworded v4 was frozen as `97de44a08a197be1594d2e12e6ff57ad95d5494d`; the hash
  above differs only in `_about`, corrected to say this.
- **Every run on v4**, aggregates only, per-case lines unread: two at its first freeze
  (fix 1; fix 2); one on the reworded set with the old code, fix 1 and fix 1 + 2 side by
  side, which sections 7 and 8 report; and one after review, fix 1 + 2 against the same
  with the corrected name rule (section 7), which changed nothing. Four in all.
- **No teammate names.** The names in the text are made up.
- **Not independent.** The engine's author wrote both sets, knowing the five problems
  above. Owner changes, cue words and openings appear because the author chose them, not
  at a measured real-world rate. Treat a result as "does the fix do what it was meant to,
  without breaking what it should not", not as an estimate of accuracy on real meetings.
- **Small.** 36 scored meetings and 60 cases. Only a large lead will be significant. The
  pair-level counts (126 pairs) and the per-segment and per-link measures give more
  observations than the headline accuracy does.

## 5. Rules for using them

- **One run per change, the old code and the new side by side**, in the same process,
  as section 10 of [`01-engine-mode-comparison.md`](01-engine-mode-comparison.md) did for
  the replacement cue. Report the paired counts and a sign test.
- **Per-case failures are not read until the change is final.** Fixes are designed on
  synthetic development data and the earlier sets, never on the two demo meetings: those
  are for finding weaknesses and testing only. Once a failure on v4 or v6 has been read
  to shape a change, that set no longer counts as held-out for the change, and this
  document says so.
- **`classic` only unless stated.** Every other mode sends the sets' text to an external
  LLM (section 6 of `01-engine-mode-comparison.md`).

```bash
uv run --package autune-context python -m autune_context.eval topic-linking \
    --dataset topic_linking_heldout_v4.json --mode classic
uv run --package autune-context python -m autune_context.eval decision-lineage \
    --dataset decision_lineage_heldout_v6.json --mode classic
```

Use the environment in section 6 of `01-engine-mode-comparison.md`, and a scratch
database.

## 6. Harness changes that came with the sets

- `topic_linking/dataset.py`: `version: 2` (`EvalSeries`, `SeriesMeeting`,
  `SeriesBlock`). Each series checks that its tags are declared and its meetings are
  oldest first. `version: 1` files load as before.
- `topic_linking/runner.py`: `run_series` uses one team per series, processes the
  meetings oldest first and returns one result per scored meeting, in categories
  `no_link`, `some_history` and `all_history`. `headline` and `report` add the two
  tag-based measures, only for a series set.
- `decision_lineage/dataset.py` and `runner.py`: an optional `context` per meeting,
  stored as utterances and cited as source utterances. Without it nothing changes.
- Tests: `tests/unit/test_eval_sets.py` (loaders, derived links, scoring helpers, the two
  files' stated counts); `tests/integration/test_eval_harness.py` (a synthetic series and
  a lineage case with context, on `fake` models, leaving no rows behind).

## 7. Fix 1 (2026-10-12): labels, openings and closings

**What changed** (`pipeline/topics.py`; checked on the sets already read and on v4):

- **Names.** `person_names` collects the words used as a name in the meeting: the word
  before a detached 님/씨 (도윤 님, 수빈 씨가) and the word before a title carrying one
  (한서진 팀장님). None of them labels a segment, anywhere in the meeting. Before, only
  the attached form (지우님) was caught. The word has to be one proper noun to
  kiwipiepy, and the detached honorific its own morpheme: in review a department before
  a title (개발 팀장님, 영업 이사님) and a common noun before a detached 님 (고객 님께,
  and 씨앗 read as 씨) had been taken for names, which dropped 개발 from "개발 일정".
- **Social nouns and adverbial nouns.** 시간, 시작, 마무리, 수고, 고생, 감사, 다행, 축하,
  죄송, 부탁, 참석, 안녕 and 환영 join the generic nouns, and a noun in -적 (기본적으로)
  breaks a phrase.
- **Openings, closings, acknowledgements.** An utterance with no noun to name anything
  is left out before segmentation when it is also two words or fewer ("네.",
  "없습니다.") or says one of the social nouns ("자, 시작합시다", "목요일에 다시 모이죠.
  수고하셨습니다"). The first version dropped every utterance with no noun. On v3 that
  lost two true links, because "매월 5일까지 제출해야 한다" and "늦으면 다음 달로
  넘어간다" are what a deadline topic is made of. The second condition was added
  after reading those two v3 cases, so v3 is a development set for this fix.

**The demo meetings.** The weaknesses this fix addresses were found on the two demo
meetings (section 1), and its first version was shaped while looking at them. Those
meetings were meant for finding weaknesses and testing only, so no result on them is
reported here, and they no longer count as a test of this fix (section 5).

**Sets already read** (old code and new in one process, case for case):

| Set | old | new | fixed / broken |
| --- | --- | --- | --- |
| topic linking `*_v2` (49) | 0.939 | 0.939 | 0 / 0 |
| topic linking held-out v3 (100) | 0.900 | 0.900 | 0 / 0 |
| brief selection `*_v1` (51) | 0.980 | 0.980 | 0 / 0 |
| brief selection held-out v1 (52) | 0.962 | 0.962 | 0 / 0 |

**Held-out v4** (the run on the reworded set, aggregates only; per-case lines not read):

| | old | new |
| --- | --- | --- |
| Segments that are an opening, closing or small talk | 10 of 80 | **7 of 76** |
| Asserted link rows made by such a segment | 24 of 237 | **16 of 209** |
| Labels naming their own topic | 26 of 80 = 0.33 | 27 of 76 = 0.36 |
| Asserted link rows on topic | 45 of 237 = 0.19 | 45 of 209 = 0.22 |
| Meeting pairs asserted (126 pairs, 44 true) | 126 | 122 |
| Accuracy (36 meetings) / pair precision / recall | 0.03 / 0.35 / 1.00 | 0.03 / 0.36 / 1.00 |

- **The fix does what it was for, and costs nothing measured.** Untagged segments go
  from 10 to 7, and the links they make from 24 to 16. No set already read moved.
- **It cannot move accuracy, because `classic` links every meeting of a team to every
  earlier one.** Before the fix it asserted all 126 pairs of v4, 82 of them false. Every
  no-link meeting got a link. Two long meetings of one team clear
  `link_similarity_threshold` on almost any pair of segments. That is problem 4 in
  section 1, and the next fix has to address it.
- **The label measure is strict.** A label counts only if it contains one of the words
  declared for its topic (`"topics"` in the file, written before any run). A label that
  names the topic in other words counts as a miss. So 0.36 is a lower bound, and the
  per-case lines that would show how loose a bound it is have not been read.
- v4 counted as held-out for fix 2: only these aggregates had been seen.
- **The narrower name rule** (above, from review) was run once more on v4, fix 1 + 2
  with the old rule against the same with the new one: identical on every measure in
  this table and in section 8's, 0 fixed and 0 broken. No set already read has a name
  the two rules disagree on.

## 8. Fix 2 (2026-10-12): a link needs the subjects to agree

**Where the false links came from.** A development series was written for this fix and
is kept apart from v4: `topic_linking_series_dev_v1.json`, three teams (a logistics
centre, a district office's civil-service team, an architecture firm) with six meetings
each, in v4's format and conventions. It shares no text with v4. On it, `classic`
asserted all 45 meeting pairs, 32 of them false. Two scores were responsible:

- **Dense similarity.** Between segments of one team's meetings it sits at a median of
  0.83 for different subjects and 0.88 for the same subject: above 0.74 either way. A
  segment's mean-pooled vector carries the team's register and shared context as much
  as its subject. Comparing a segment with the other segments of its own meeting as a
  baseline separated nothing either (margins 0.10 against 0.12).
- **The re-ranker.** Unrelated segments scored 1.000 whenever they shared a stretch of
  near-identical text, such as an opening line or a stock phrase. On its own, that score
  was enough to assert a link.

**What separated subjects** on the development data (pair-level AUC: development
series / `_v2` / held-out v3): the segment vector 0.89 / 0.995 / 0.985; the top five
noun phrases embedded as one string 0.98 / 0.99 / 0.995; shared content nouns 0.99 /
0.92 / 0.97. The phrases leave the register out, and shared nouns catch the talk that
the phrases word differently.

**What changed** (`service._subjects_agree`, `HybridRetriever.segment_texts`,
`topics.keyphrases`, `topics.content_nouns`): a link that either score would assert is
asserted only if the topic's keyphrases reach `link_keyphrase_threshold` (0.55) cosine
with one of the past meeting's segments, or share `link_shared_nouns` (4) content nouns
with one. Otherwise it is written as `pending`, a question to the user, as before. Every
segment of the past meeting is tried, consented text only and read at link time. A first
version tried only the candidate's passage and applied the check to similarity alone; on
the development series that left 24 false pairs. Both thresholds were picked on the
development series, `_v2` and v3. Nothing was picked on v4.

**Development data and sets already read** (old: the check off; new: on; one process,
same cases):

| Set | accuracy old → new | fixed / broken | true pairs reaching the user |
| --- | --- | --- | --- |
| development series (15) | 0.00 → 0.67 | 10 / 0 (p = 0.002) | 13/13 → 13/13 |
| topic linking `*_v2` (49) | 0.939 → 0.898 | 2 / 4 | 38/38 → 38/38 |
| topic linking held-out v3 (100) | 0.90 → 0.91 | 6 / 5 | 81/81 → 81/81 |
| brief selection `*_v1` and held-out v1 | unchanged | 0 / 0 | — |

The cases `_v2` and v3 lose are mostly paraphrases ("서버 응답 속도가 느려졌다" against "앱이 버벅인다"). Their
keyphrases are worded too differently to reach 0.55 and they share few nouns. Each of
them is still written as a pending link, which is why every true pair still reaches the
user. Precision on v3 is 1.00, and no no-link meeting there gets a false link.

**Held-out v4** (the run on the reworded set, aggregates only; per-case lines not read):

| | fix 1 only | fix 1 + fix 2 |
| --- | --- | --- |
| Accuracy (36 meetings) | 1/36 = 0.03 | **20/36 = 0.56** |
| Fixed / broken | | **19 / 0, sign test p = 0.000004** |
| Meeting pairs asserted (126; 44 true) | 122 (78 false) | **62 (21 false)** |
| True pairs asserted / reaching the user | 44 / 44 | 41 / **44** |
| No-link meetings given a link | 12 of 12 | 6 of 12 |
| Asserted link rows on topic | 45 of 209 = 0.22 | 40 of 72 = 0.56 |
| Asserted link rows made by an opening, closing or small talk | 16 | 4 |

- **This is the first fix in this document a held-out sign test establishes for topic
  linking.** On meetings of realistic length in a team's series, `classic` goes from
  linking everything to linking about half of what it did, and 41 of the 44 true pairs
  are among what it still links. The other three are pending.
- **It is not done.** 21 false pairs remain, and half the no-link meetings still get a
  link. Accuracy of 0.56 is far below what v3 suggested for `classic` (0.90), because v3
  cannot show this failure.
- **The cost lands on paraphrase.** On sets already read, a true link worded with
  different nouns moves from asserted to pending. Nothing true was lost from the user's
  view on any set.
- v4 has now been run four times, aggregates only (section 4), and still counts as
  held-out for the next fix.

## 9. Fix 3 (2026-10-12): decision lineage reads a decision's source utterances

**Development data.** `decision_lineage_context_dev_v1.json` (30 cases) is v6's format
with other subjects: a library, a bus company, a theatre, a kindergarten, a bakery, a
sports club. It shares no statement with v6 or any earlier set. On it, today's code
threaded 18 of 30, and every miss was a terse statement or a distractor. Embedding each
statement together with its source utterances and taking the higher of the two cosines,
at the existing `lineage_match_threshold` (0.65), threaded 28 of 30. At 0.60 it threaded
29 but put a new decision on an old thread, so the threshold stays. Classifying the
change type on the source utterances was also tried: on the closest utterance 15 of 22
were right, on the statement plus the last utterance 16 of 22, and on the statements
alone 17 of 22. So change type stays on the statements.

**What changed** (`service._context_vectors`, `_ThreadHead.context_vector`,
`_assign_decisions_to_threads`): a (decision, thread head) pair scores the higher of the
statements' cosine and the cosine of each statement embedded together with its source
utterances: B's `source_utterance_ids` for the decision, the stored ids for the head.
Utterances are read at match time and only from speakers who consent. A side with none
readable uses its statement, so a set without `context` (v1–v5) scores exactly as before.
No new threshold. `llm` mode is unchanged and reads no utterance: the judge reads
statements, so only the cosine path fetches sources.

**Development set** (old: utterances not read; new: read; one process, same cases):
accuracy 0.567 → 0.800, 7 fixed and 0 broken (p = 0.016); threading 0.60 → 0.93.

**Held-out v6** (one run, the final code, aggregates only; per-case lines not read):

| | utterances not read | read |
| --- | --- | --- |
| Accuracy (60 cases) | 34/60 = 0.567 | **48/60 = 0.800** |
| Fixed / broken | | **14 / 0, sign test p = 0.0001** |
| Threading | 0.650 | **0.967** |
| Change type among correctly threaded non-new | 0.815 | 0.783 |
| `terse_modified` / `terse_reversed` / `terse_unchanged` | 0/6 · 0/5 · 0/5 | 5/6 · 2/5 · 2/5 |
| `terse_new` (must stay new) | 6/6 | 6/6 |
| `distractor` / `chain` | 3/6 · 2/4 | 6/6 · 4/4 |
| `unchanged` / `modified` / `reversed` / `new_same_domain` | 6/6 · 6/8 · 6/8 · 5/6 | the same |

- **Established on held-out data.** 14 cases fixed, none broken. Reading the utterances
  did not put a single unrelated decision on a thread: all six `terse_new` cases stay new.
- **What is left is change type, not threading.** Terse decisions now find their thread
  (threading 0.97), but "없던 걸로 한다" or "지금대로 간다" against the earlier full
  statement is often classified wrong. NLI sees two sentences that share nothing.
  Change type among threaded cases dips (0.815 → 0.783) because 22 terse cases now reach
  classification at all. Fixing that needs a set that v6 is not.
- v6 has now been run once, aggregates only, and still counts as held-out for a
  change-type fix.
