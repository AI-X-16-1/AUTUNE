# Evaluation 02 — does the pre-meeting brief recap the right meeting?

**Measured:** 2026-10-12 · **Written:** 2026-10-12 · **Owner:** 문민재 ·
**Module:** Meeting Context Engine (`autune_context`)

The question: *when a scheduled meeting is about to start, does the brief pick
the past meeting this one follows?* That choice
(`briefs.choose_previous_meeting`) is the brief's only judgement; everything
else is a template over stored rows. It tries three steps in order: `series`
(the most recent analyzed meeting with the same title, ignoring spacing and
case), `topic` (hybrid retrieval over the title and agenda, re-ranked, asserted
on topic linking's own thresholds), and `latest` (the team's most recent
meeting). Under `latest` the Slack brief names that meeting but posts none of
its content, since nothing tied the two.

The short answer: **the brief never recapped an unrelated meeting, and it
recapped the right one whenever the titles matched. When they did not, it
missed 20 of 27 related meetings and fell back to a recap-less brief.** The
cause is that the topic step reuses topic linking's thresholds on a query that
is only a title and a few agenda lines.

| | Right | Notes |
| --- | --- | --- |
| **All 51 cases** | **30/51 = 0.59** (95% CI 0.45–0.71) | lenient (any meeting on the subject) 31/51 = 0.61 |
| Same title (`series_exact`, `series_variant`) | 10/10 | all answered by the series step |
| No related meeting (`unrelated`, `same_domain`, `shared_keyword`) | 14/14 | **0 unrelated meetings recapped** |
| Related meeting, different title | 7/27 | 20 fell back to `latest`, 1 picked the older meeting of the series |

## Setup

- Suite `brief-selection`, dataset `brief_selection_v1.json`: 51 synthetic cases,
  written and labeled before any model ran on them. Each case is a team's
  analyzed past meetings (one to six, three utterances each, all with real
  titles) and one meeting scheduled ten minutes ahead with a title and an
  optional agenda.
- `expected` is the meeting this one follows, or none. `related` lists every
  past meeting on the same subject. A case is right when the brief asserts
  `expected` (via `series` or `topic`), or, when nothing is related, falls back
  to `latest`. Getting `expected` through `latest` by coincidence counts as a
  miss, because the brief then posts no content.
- `engine_mode=classic`, local models (`kure_v1_local`,
  `bge_reranker_v2_m3_ko_local`) on CPU, one run. These models are
  deterministic, so a rerun gives the same scores.
- Configured thresholds: `link_similarity_threshold = 0.74`,
  `link_confidence_threshold = 0.6`, `rerank_top_k = 10`.

Run it with:

```
uv run --package autune-context python -m autune_context.eval brief-selection
```

It needs a throwaway database migrated to `heads`; the guard refuses one that
holds real meetings.

## By category

| Category | What it tests | Right |
| --- | --- | --- |
| `series_exact` | same title, sometimes with another meeting more recent | 6/6 |
| `series_variant` | title differs only in spacing or case | 4/4 |
| `numbered_series` | "2차 → 3차", "스프린트 15 → 16", "10/7 → 10/14 주간회의" | **0/8** |
| `topic_retitled` | different title, same subject | 2/8 |
| `generic_title` | "정기 회의", "회의": only the agenda names the subject | 1/4 |
| `distractor` | several same-area meetings, the related one not the latest | 3/7 |
| `unrelated` | nothing related | 6/6 |
| `same_domain` | same area, different subject | 4/4 |
| `shared_keyword` | a shared word with another meaning ("배포", "모델", "카드") | 4/4 |

## What the numbers say

1. **The failure is safe but leaves the brief empty.** Every miss fell back
   to `latest`, so no one received another group's content. But the brief
   then carries no recap, so a team whose meetings are titled by round or date
   gets an empty brief every time. The example that started this evaluation
   ("v2.0 출시 준비 1차/2차 회의" → "3차") fails this way.
2. **The topic step's threshold is out of range for this query.** Topic
   linking compares one topic segment with another, and 0.74 was set on that.
   Here the query is a title and a few agenda lines. The expected meeting's
   dense similarity was 0.45–0.78 and reached 0.74 in only 6 of 27 cases. The
   re-ranker scores nearly every title query near 0. It cleared 0.6 for the
   expected meeting only in the four cases where the agenda repeated that
   meeting's words (`ga_onboarding`, `di_payment`, `di_hr`, `di_dark_mode`),
   and once for the wrong one (`tr_client_phases`). The highest similarity any
   no-related case reached was 0.560.
3. **The topic step picks the best-scoring meeting, not the most recent one
   on the subject.** In `tr_client_phases` it recapped the kick-off instead of
   the mid-point check that came after it. At lower thresholds the same rule
   would pick "1차" over "2차" (`ns_launch_round` 0.637 vs 0.612) and the
   older of two related meetings in `ns_sprint_retro` and `di_two_related`.
4. **Number, round and date titles are a title problem, not a retrieval
   problem.** `ns_dated_weekly` cannot be solved by retrieval: the two weekly
   meetings covered different subjects, and the upcoming one has no agenda.
   Only the title links them.

The threshold sweep in the run's output puts the best similarity threshold
at 0.575 (45/51, still 0 unrelated recapped). **That is not a result:** it was
read off this set's failures. The margin is also thin: the highest no-related
case scored 0.560, and below 0.575 unrelated meetings start being recapped
(4 at 0.55, 9 at 0.50).

## What would change it (not done here)

- A brief-specific similarity threshold, instead of topic linking's.
- Among confident candidates, prefer the most recent meeting on the subject
  over the highest-scoring one.
- In the series step, treat titles that differ only in a round, number or date
  ("N차", "스프린트 N", "M/D", "N월", "N주차") as one series.

Each change needs a new held-out set (`brief_selection_heldout_v1`) written
before it is measured. v1 can no longer serve as one, because its failures
are now known.

## Caveats

- Synthetic and small: 51 cases with three short utterances per meeting, each
  written to fall in one topic segment. Real meetings are longer and have
  several segments, which changes what retrieval finds.
- Written with an AI assistant from the owner's session, and labeled by the
  same process. No second labeler.
- `classic` only; `llm` and `hybrid` were not run. The brief's topic step calls
  the re-ranker directly in every mode, so they could differ only through the
  past meetings' topic rows.
