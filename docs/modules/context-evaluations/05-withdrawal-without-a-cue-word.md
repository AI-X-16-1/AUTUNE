# Evaluation 05 — a withdrawal said without a cue word

Date: 2026-10-13. Mode: `classic`, local models (section 6 of
[`01-engine-mode-comparison.md`](01-engine-mode-comparison.md)), scratch databases.

Fix 4 ([`04-terse-decision-change-type.md`](04-terse-decision-change-type.md)) left one
gap open. A decision taken back without a negation or a cancellation noun was read as
`modified`: "배달 앱에서는 빠지죠", "계약을 끊고 …에 넘기죠", "다시 카운터에서 주문
받죠". v7's `terse_reversed` stayed at 6/12. Those words are no part of the replacement
or cancellation cues (`pipeline.change`), and reading the source utterances cannot help
when the utterance does not say "cancel" either. This document covers fix 5, a cue for
those withdrawals, and the two sets written to build and measure it.

## 1. The sets

| File | Role | Cases | Frozen as (`git hash-object`) |
| --- | --- | --- | --- |
| `decision_lineage/fixtures/decision_lineage_heldout_v8.json` | held-out | 60 | `2a41858493d62a2456bacb7ba6305b866ce04b20` |
| `decision_lineage/fixtures/decision_lineage_withdrawal_dev_v1.json` | development | 40 | — |

**Held-out v8** was written and frozen before fix 5 was designed and before anything ran
on it. It has:

- 25 withdrawals without a cue word: 11 leave something (닫다, 빠지다, 끝내다, 손 떼다,
  해지하다), 7 go back to how things were, 7 replace the earlier choice without a swap
  verb.
- 3 withdrawals with one, kept as a control.
- 14 traps, cases a withdrawal cue could misread. 11 are modifications: a part left out
  ("…은 빼고"), a closing time moved, a date moved on, a rota, "다시" with a new value. 3
  are keeps said with 원래대로, 예전처럼 or 하던 대로.
- 7 plain modifications, 4 plain keeps and 7 new decisions.

Four cases add a second, unrelated past decision of the same team. Every meeting carries
`context`, as in v6 and v7. The subjects are a public library, a fitness gym, a newsroom,
a festival committee, a car repair shop, a museum and a community orchestra.

**The development set** was written after v8 was frozen, with other subjects: a ski
resort, a language academy, a veterinary clinic, a farmers' market, a co-working space and
a city bus company. It has 18 withdrawals without a cue word, 1 with one, 11
modification traps, 5 keep traps and 5 new decisions. Fix 5 was designed on it and on the
two development sets of fixes 3 and 4.

Neither set repeats a statement or a line from an earlier set; a script checked both. One
v8 statement did repeat a statement of `decision_lineage_context_dev_v1.json` and was
reworded before the freeze. Neither takes anything from the development meetings. The
names in them are made up. Both were written by the engine's author, who also wrote the
cue, so they are not independent: the caveats in section 4 of
[`03-realistic-heldout-sets.md`](03-realistic-heldout-sets.md) apply, more so here, since
v8 was written with the gap in mind.

## 2. What the development data showed

Before the fix, the development set scored 19/40. All 18 withdrawals without a cue word
read wrong: 15 as `modified`, and 3 as `unchanged`. The 3 were all leaving verbs ("…은
이번 시즌으로 끝냅시다", "…은 이제 손 뗍시다", "…은 그만한다"), and NLI read them as
*entailed* by the decision they took back, at 0.81 to 0.92 in one direction. The two
statements share a subject and little else that the model weighs. A cue that only
spoke where NLI contradicts would have missed them.

Three more things showed in the per-case lines:

- In two cases the utterance read in the statement's place (fix 4's
  `_change_hypotheses`) was a reason, not the decision ("지정석은 절반이 비어 있어요"),
  because it shared the subject's words. The statement itself said "자유석으로
  돌아간다". So a withdrawal said in either one has to count.
- "예전처럼 …" said against a decision that changed how things were contradicts it and
  takes it back. Said against a decision NLI agrees with, the same words keep it.
- A value as the verb's argument marks a parameter: "9시에 닫는다", "네 명을 뺀다",
  "일곱 개로 정리한다". A number that names the subject does not: "24시간 운영은
  종료한다".

## 3. What changed

All in `pipeline.change`, plus one argument through `service._classify_pairs_nli`.

1. **A withdrawal said by leaving or going back reads `reversed`, before NLI**
   (`marks_withdrawal`). The verbs are general Korean for leaving and going back: 끝내다,
   닫다, 빼다, 빠지다, 끊다, (손) 떼다, 내려놓다, 그만하다, 돌아가다, 되돌리다, and the
   nouns 종료, 해지, 폐지, 폐선, 폐쇄, 폐업, 철수, 해산, 탈퇴, 반납, 단종, 퇴출, 복귀,
   원상복구, 환원 with 하다. The verb counts only:
   - as the head of the statement's last clause: "빼고 …" and "빼지 않는다" hand the
     clause on;
   - with no value as its object or its 에-phrase, and no number or counter in a
     로/까지-phrase ("이번 분기로 끝낸다" is when it ends, "7시로" is a value);
   - when the earlier statement did not say the same word ("야간에 닫는다" -> "계속
     닫는다");
   - when the statement is not a keep (`marks_keep`).
2. **Bringing in-house what was given out reads `reversed`** (`marks_in_house`): the
   earlier statement has an outsourcing word (위탁, 외주, 외부, 업체, 대행, 용역, 협력,
   납품, 임대, 맡기다), and the later one has 직접, 자체, 사내, 내부, 원내, 직영 or 자사.
3. **Both are read on the version's own statement as well as on the line read in its
   place.** `_classify_pairs_nli` passes the statements through; `classify_change` takes
   `statement=`.
4. **Going back to how things were reads `reversed` where NLI contradicts**
   (`marks_as_before`: 원래, 예전, 종전, 이전 + 대로/처럼). Where NLI is neutral, the same
   words are still a keep (`marks_keep`). 지금대로 and 현행처럼 are the present and never
   a return.
5. **넘기다 and 돌리다 join the swap verbs** of the replacement cue. Their target decides,
   as for the others: "시설공단에 넘긴다" and "직영으로 돌린다" name a new thing, "다음
   주로 넘긴다" and "3교대로 돌린다" a value.

정리하다 and 마무리하다 were in the first list and came out of it. An existing
integration test, whose terse statement is "그렇게 정리했다", read as a withdrawal. That
phrase is how a meeting says it settled something, far more often than "그 서비스는
정리하죠" withdraws one. One development case was lost with them.

## 4. Results

Old code against new, one process, same cases: A is `pipeline.change` as on main, and B
is the shipped code (`service.classify_change` swapped between them).

**Development sets** (per-case lines read):

| Set | A | B | Fixed / broken |
| --- | --- | --- | --- |
| `decision_lineage_withdrawal_dev_v1.json` (40) | 0.475 | **0.775** | 12 / 0, p = 0.0005 |
| `decision_lineage_terse_dev_v1.json` (42) | 0.857 | **0.929** | 3 / 0 |
| `decision_lineage_context_dev_v1.json` (30) | 0.867 | **0.900** | 1 / 0 |

On the withdrawal set, leaving verbs went from 0/7 to 7/7 and going back from 0/5 to 4/5.
Replacements without a swap verb went from 0/6 to only 1/6: an alternative said with an
ordinary verb ("각자 받는다", "캡슐 머신으로 해결한다") carries no word to read. No trap
moved in either direction.

**Earlier sets, statements only** (aggregates):

| Set | A | B | Fixed / broken |
| --- | --- | --- | --- |
| `decision_lineage_v2` (50) | 1.000 | 1.000 | 0 / 0 |
| held-out v1 (30) | 0.967 | 0.967 | 0 / 0 |
| held-out v2 (26) | 0.846 | 0.846 | 0 / 0 |
| held-out v3 (100) | 0.920 | 0.930 | 1 / 0 |
| held-out v4 (120) | 0.767 | 0.783 | 3 / 1 |
| held-out v5 (120) | 0.817 | 0.842 | 3 / 0 |
| held-out v6 (60) | 0.900 | 0.917 | 1 / 0 |

Together 8 fixed and 1 broken. These sets were used by earlier fixes and are no longer
held-out for change type; they are here to catch what the cue breaks. The broken case is
in v4. Its line has not been read.

**Held-out v8** (one run, the final code, aggregates only; per-case lines not read):

| | A | B |
| --- | --- | --- |
| Accuracy (60) | 26/60 = 0.433 | **35/60 = 0.583** |
| Fixed / broken | | **9 / 0, sign test p = 0.004** |
| Threading | 0.917 | 0.917 |
| `reversed_uncued_exit` | 0/11 | **7/11** |
| `reversed_uncued_return` | 1/7 | 2/7 |
| `reversed_uncued_replace` | 0/7 | 1/7 |
| `reversed_cued` | 2/3 | 2/3 |
| `modified_trap` / `unchanged_trap` | 7/11 · 2/3 | 7/11 · 2/3 |
| `modified` / `unchanged` / `new` | 6/7 · 1/4 · 7/7 | the same |

**Held-out v7** (its second run, aggregates only). Section 5 of
[`04-terse-decision-change-type.md`](04-terse-decision-change-type.md) kept it held-out
for a fix that does not tune fix 4's three parts; this one does not:

| | A | B |
| --- | --- | --- |
| Accuracy (60) | 45/60 = 0.750 | **49/60 = 0.817** |
| Fixed / broken | | **4 / 0, p = 0.125** |
| `terse_reversed` | 6/12 | **9/12** |
| `reversed` (full statements) | 4/6 | 5/6 |
| every other category | | the same |

A is 0.750, the same as fix 4's shipped number on v7. Pooled over the two held-out sets:
**13 fixed, 0 broken, sign test p = 0.0002**.

- **Established on held-out data.** A withdrawal said by leaving something now reads as
  one: 0/11 → 7/11 on v8, and v7's `terse_reversed` 6/12 → 9/12. No case broke on
  either set, and no trap moved, so the conditions on the verb held on text the cue was
  not designed on.
- **Not established.** Going back (1/7 → 2/7) and replacing without a swap verb (0/7 →
  1/7) barely moved on v8, though going back went from 0/5 to 4/5 on the development set.
  The development set's returns used a return verb or 예전처럼 where NLI contradicts.
  Whichever ways v8 says them, the cue mostly did not read them. Which ways those are is
  not known without reading v8's lines.
- **What v8 shows besides.** A scores only 0.433 on v8. The withdrawals explain most of
  that, but not all: its plain keeps scored 1/4 and its modification traps 7/11 before
  and after, and 5 cases did not thread. Those failures are older than this fix, and v8
  now measures them for a later one.

## 5. What the sets are worth now

- v8 has been run once, aggregates only. It still counts as held-out for a fix that does
  not tune the withdrawal cue, for example threading, keeps, or replacements said with an
  ordinary verb. The next run must again put old and new code side by side.
- v7 has been run twice, aggregates only, for fixes 4 and 5. It no longer counts as
  held-out for change type.
- The withdrawal development set is development data. Its per-case lines have been read.

```bash
uv run --package autune-context python -m autune_context.eval decision-lineage \
    --dataset decision_lineage_heldout_v8.json --mode classic
```
