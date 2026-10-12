# Evaluation 03 — held-out sets for the next round of fixes

**Written:** 2026-10-12 · **Owner:** 문민재 ·
**Module:** Meeting Context Engine (`autune_context`) · **Status:** sets frozen, nothing
run on them yet

Two development meetings, run through the engine in `classic` mode, showed five weaknesses
that the existing evaluation sets do not catch. The sets are short, synthetic and mostly
one topic per meeting. This document describes two new held-out sets written to measure
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

These two meetings are the development examples for the fixes. Neither held-out set
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
| `terse_unchanged` / `terse_modified` / `terse_reversed` | 5 / 6 / 5 | the statement leaves out what it is about ("B안으로 확정한다", "2주 미루기로 했다", "없던 걸로 한다"); only the context says it |
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
  | `topic_linking/fixtures/topic_linking_heldout_v4.json` | `97de44a08a197be1594d2e12e6ff57ad95d5494d` |
  | `decision_lineage/fixtures/decision_lineage_heldout_v6.json` | `438ea5b11f728a94f03bdae7af52d986c5665618` |

- **No overlap.** A script compared every statement, context line and tagged line with
  every earlier fixture, and with the two development meetings, using character bigrams.
  It flagged three near-copies of earlier lineage sets, which were rewritten. A first
  draft had one series and ten lineage cases built on the development meetings; they were
  rewritten in other domains before freezing. In review two more kinds of line were found
  close to the development meetings: one terse decision in v6 and the closing formula
  three teams of v4 used. Both were reworded, still before any run, and the sets were
  frozen again at the hashes above.
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
- **Per-case failures are not read until the change is final.** Fixes are designed from
  the development meetings and the earlier sets. Once a failure on v4 or v6 has been read
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
