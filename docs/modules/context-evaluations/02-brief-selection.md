# Evaluation 02 — does the pre-meeting brief recap the right meeting?

**Measured:** 2026-10-12 · **Written:** 2026-10-12 · **Owner:** 문민재 ·
**Module:** Meeting Context Engine (`autune_context`)

The question: *when a scheduled meeting is about to start, does the brief pick
the past meeting this one follows?* That choice
(`briefs.choose_previous_meeting`) is the brief's only judgement; everything
else is a template over stored rows. It tries three steps in order: `series`
(a past meeting with the same title), `topic` (hybrid retrieval over the
title and agenda, re-ranked), and `latest` (the team's most recent meeting).
Under `latest` the Slack brief names that meeting but posts none of its
content, since nothing tied the two.

The short answer: **before the fix, the brief recapped the right meeting only
when the titles matched exactly, and fell back to a recap-less brief for 27 of
37 related meetings on the held-out set. After the fix it recaps 35 of 37,
and in neither version did it recap an unrelated meeting.**

| Held-out set (52 cases) | Before | After |
| --- | --- | --- |
| **Accuracy** | 25/52 = 0.48 (95% CI 0.35–0.61) | **50/52 = 0.96** (95% CI 0.87–0.99) |
| A related meeting exists (37) | 10/37 | **35/37** |
| No related meeting (15): unrelated meeting recapped | 0 | **0** |
| Cases right only after / only before | | 25 / 0 (sign test p < 0.001) |

## Setup

- Suite `brief-selection`. Two sets of synthetic cases, each labeled before any
  model ran on it:
  - `brief_selection_v1.json` (51 cases): the dev set. The first measurement
    was taken on it, and the fix was designed and its threshold chosen from it.
  - `brief_selection_heldout_v1.json` (52 cases): written and committed after
    the first measurement and before any fix. The fixed code was run on it
    once; the code before the fix was run on it once for comparison.
- Each case is a team's analyzed past meetings (one to six, three utterances
  each, all with real titles) and one meeting scheduled ten minutes ahead,
  with a title and an optional agenda.
- `expected` is the meeting this one follows, or none. `related` lists every
  past meeting on the same subject. A case is right when the brief asserts
  `expected` (via `series` or `topic`), or, when nothing is related, falls back
  to `latest`. Getting `expected` through `latest` by coincidence counts as a
  miss, because the brief then posts no content.
- `engine_mode=classic`, local models (`kure_v1_local`,
  `bge_reranker_v2_m3_ko_local`) on CPU, one run per configuration. These
  models are deterministic.

Run it with:

```
uv run --package autune-context python -m autune_context.eval brief-selection
uv run --package autune-context python -m autune_context.eval brief-selection --dataset brief_selection_heldout_v1.json
```

It needs a throwaway database migrated to `heads`; the guard refuses one that
holds real meetings.

## First measurement (dev set, before the fix)

30/51 = 0.59. The series step was right 10/10, and no unrelated meeting was
recapped (0 of 14). Where the title differed from the earlier meeting's, the
topic step asserted only 7 of 27 related meetings. Every numbered series
("2차 → 3차", "스프린트 15 → 16", "10/7 → 10/14 주간회의") fell back to
`latest`: 0/8.

Three causes:

1. **The topic step's threshold was out of range for this query.** It reused
   topic linking's dense threshold (0.74), which was set on comparing one topic
   segment with another. Here the query is a title and a few agenda lines. The
   expected meeting's similarity was 0.45–0.78 and reached 0.74 in only 6 of
   27 cases. The re-ranker scores a title query near 0 unless the agenda
   repeats the past meeting's words. No unrelated meeting scored above 0.560.
2. **It picked the best-scoring meeting, not the most recent one on the
   subject.** In `tr_client_phases` it recapped the kick-off instead of the
   check-in that followed it.
3. **A numbered title is a title problem, not a retrieval problem.** In
   `ns_dated_weekly` the two weekly meetings covered different subjects and
   the upcoming one has no agenda. Only the title links them.

## The fix

1. **Series step:** if no past title matches exactly, compare titles with
   their round, number or date removed (`briefs._series_key`): "N차", "N회차",
   "N주차", "N번째", "N분기", "N월", "M/D", "YYYY.MM.DD", "#N", and the number
   after "스프린트"/"Sprint". A bare number elsewhere ("1팀", "고객사 2") and a
   version ("v2.0") stay, because they name a different group or release.
   An exact match still wins.
2. **Topic step threshold:** `brief_similarity_threshold = 0.60` instead of
   0.74. Chosen on the dev set, where 0.575 and 0.60 tied after the fix. 0.60
   was taken for its margin above the highest unrelated score (0.560).
3. **Topic step pick:** the most recent confident meeting, not the
   best-scoring one.

| Dev set (51 cases) | Before | After |
| --- | --- | --- |
| Accuracy | 30/51 = 0.59 | 50/51 = 0.98 |
| `numbered_series` | 0/8 | 8/8 |
| `topic_retitled` | 2/8 | 8/8 |
| `distractor` | 3/7 | 7/7 |
| Unrelated meeting recapped | 0 | 0 |

The dev set's number after the fix is not a result, because the fix was
designed on that set. The held-out numbers above are the result.

## Held-out, by category

| Category | Before | After |
| --- | --- | --- |
| `series_exact` | 4/4 | 4/4 |
| `series_variant` | 2/2 | 2/2 |
| `numbered_series` | 0/12 | **12/12** |
| `topic_retitled` | 2/8 | 7/8 |
| `generic_title` | 1/4 | 4/4 |
| `distractor` | 0/4 | 3/4 |
| `recent_related` | 2/4 | 4/4 |
| `unrelated`, `same_domain`, `shared_keyword` | 12/12 | 12/12 |
| `numbered_trap` ("1팀"/"2팀", "고객사 1"/"고객사 2") | 2/2 | 2/2 |

The two held-out misses both fell back to `latest`, so the brief carried no
content:

- `h_tr_recruit_event`: 0.594, just under 0.60. "캠퍼스 리크루팅 발표 자료"
  vs "대학 채용 설명회".
- `h_di_data`: 0.565. "실험 도구 도입 일정" vs "A/B 테스트 플랫폼".

## What is still thin

- **The margin is narrow.** On the held-out set the highest score for an
  unrelated meeting was 0.588 (`h_sd_payment`), against the threshold of 0.60.
  At 0.575, two unrelated meetings would have been recapped, and the two
  misses sit at 0.594 and 0.565. Related and unrelated scores overlap around
  0.55–0.60, and no threshold separates all of them. A miss costs a brief its
  recap; a false match posts another meeting's topics and decisions to the
  channel. The threshold sits at the cautious end of the overlap for that
  reason.
- **The held-out set was written knowing what the fix would be.** The title
  formats in `numbered_series` were written by the same person who wrote the
  series rule, so 12/12 shows that the rule covers those formats, not that it
  generalizes to formats nobody thought of. `numbered_trap` checks the
  opposite direction on only two cases.
- **The data is synthetic and short.** Three utterances per meeting, written
  to fall in one topic segment. Real meetings are longer and have several
  segments, which changes what retrieval returns and how it scores. Re-check
  the threshold on masked real meetings before trusting it further.
- **One labeler:** the cases were written and labeled with an AI assistant in
  the owner's session. No one else labeled them.
- **Only `classic` was run.** The brief's topic step calls the re-ranker
  directly in every mode, so `llm` and `hybrid` could differ only through the
  past meetings' topic rows.
- **Both sets are now spent for tuning.** Any further change to the rule or
  the threshold needs a new held-out set.
