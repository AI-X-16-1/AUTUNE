# 04 — Numbers read aloud: what Whisper writes, and what the masker misses (#160)

**Date:** 2026-10-02. **Model:** the stored path's `transcribe()`, `faster-whisper large-v3`, int8 on CPU, no glossary. **Masker:** `mask()` with the spoken-number recogniser, as the pipeline runs it.

## Why

#160 lists number shapes neither `find_pii` nor the recogniser reads — groups split by commas, one syllable at a time, a filler between groups — and asks for a count before paying for a fix (a shared pattern widened, or offset mapping in the recogniser).

## 1. There was nothing to count

`modules/audio/scripts/count_number_shapes.py` over every transcript on hand:

| Source | Utterances | Runs of 7+ numerals | comma / one-at-a-time / filler |
| --- | --- | --- | --- |
| Stored utterances, every local database, deduplicated | 218 | 1 (masked) | 0 / 0 / 0 |
| HiKE human references | 1,121 | 0 | 0 / 0 / 0 |

Neither source has people reading numbers. "Zero" here means *no data*, not *rare*.

## 2. So the numbers were made

`modules/audio/scripts/probe_spoken_numbers.py --per-kind 4`: generated phone, account (3-2-6) and resident-number (6-7) values, each read in a sentence ("제 번호는 … 입니다") in four styles — grouped (`공일공 일이삼사 오육칠팔`), comma-paused, one syllable at a time with silences, and with `음` between groups — by macOS TTS (Yuna, the one Korean voice that speaks on a stock machine) at 150 and 220 wpm. 96 clips. A clip **leaks** when more of the number's digits are readable after masking than when the same number is written as digits (`010-1234-5678`) and masked.

| Kind | grouped | comma | one at a time | filler | Total |
| --- | --- | --- | --- | --- | --- |
| phone | 0/8 | 0/8 | 0/8 | 0/8 | **0/32** |
| account | 6/8 | 4/8 | 0/8 | 5/8 | **15/32** |
| resident number | 5/8 | 3/8 | 1/8 | 4/8 | **13/32** |

## What it says

1. **Whisper never wrote a digit syllable.** All 96 transcripts are Arabic digits. The comma and one-syllable shapes #160 worries about did not occur in its output; the recogniser's input (`공일공`) did not occur either. On this evidence #160's two shapes are not where the risk is.
2. **The risk is Whisper's grouping.** It inserts hyphens where it guesses groups are, and the guess is often wrong for anything that is not a phone number: an account read 3-2-6 came back `450-80-930-9782`, a resident number `971-227-837-6573`, `700826-643-8793`. The patterns key on group lengths (an RRN is 6+7, an account three groups of 2–6), so a mis-grouped value matches nothing and is stored in the clear, or matches `account` and keeps its outer groups.
3. **A filler survives as text glued to the digits** — `628음 84음 919160`, `971227음 8376573` — and nothing matches across it.
4. **Phones are fine** in every style: Whisper groups them 3-4-4 (or close), which is the shape the phone pattern accepts.

Separately, while checking: a correctly written account with a seven-digit last group (`450-80-9309782`) matches no pattern — `account` allows at most six digits per group.

## What this measurement cannot see

- Real speech: TTS is even, never mumbles, pauses exactly where told, one voice. How people actually read numbers in meetings is still unmeasured.
- Frequency: how often a meeting contains an account or resident number at all.
- The live path (`large-v3-turbo`), which may group differently.

## Where the effort should go

Not #160's shapes. The leak is **numbers whose grouping Whisper invents**, which a length-keyed pattern cannot follow. The candidate is a catch-all for a run of digit groups joined by hyphens or spaces whose *total* is long enough to be an identifier, regardless of how it is split — the shape `digits` already covers for runs with no separator. Its cost is the false-positive question #125 and #148 already met (`2024-2025-2026` is twelve digits), so it needs the corpus run before and after, and a decision. Filed separately.
