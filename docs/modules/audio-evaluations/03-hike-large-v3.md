# Evaluation 03 — `large-v3` on HiKE, the Korean-English code-switching benchmark

**Date:** 2026-09-17 · **Owner:** 김민경 · **Module:** A, Audio Pipeline

Evaluations 01 and 02 measured the pipeline on a recording we made ourselves.
That recording can say how the glossary prompt, diarization and masking behave,
but it cannot say how the model survives a language switch, and every Autune
meeting switches. This evaluation puts the same `transcribe()` in front of a
public benchmark built for exactly that question, and reads the answer against
the benchmark's own published table.

The short answer is that the model is where the literature says it should be,
and that the one production setting we pin — `language="ko"` — is free on
Korean sentences and expensive on English ones.

---

## 1. What was measured

| | |
| --- | --- |
| **Corpus** | HiKE, `thetaone-ai/HiKE` on Hugging Face (Apache-2.0). 1,121 utterances, 2.22 h, 13 bilingual speakers, one `test` split |
| **Labels** | Reference text; the same text with `<tag …>` around each switch point; a code-switching level per row (word 457 · phrase 607 · sentence 57); a topic (8, including software development 162 and business 169); loanwords with both spellings |
| **Model** | `large-v3`, faster-whisper 1.2.1, CTranslate2 int8, CPU — the production build |
| **Settings** | The pipeline's `transcribe()` with **no glossary** (HiKE has no meeting vocabulary to build one from). Two runs over all 1,121 rows: `language="ko"`, which is what production does, and language detection, which is how HiKE ran Whisper |
| **Scored by** | `autune_audio.eval.codeswitch` — MER and PIER reimplemented from HiKE's code and pinned against it (`tests/unit/fixtures/hike_fidelity.json`: 1,168 of 1,181 predictions score identically; the 13 that differ are two documented, measured divergences) — plus `korean.character_error_rate` for continuity with evaluation 01 |

Two metrics are HiKE's, and they are worth knowing apart. **MER** (mixed error
rate) is word error rate over a mixed tokenisation: every Hangul syllable is a
token, every latin word is a token, so it is CER for the Korean and WER for the
English in one number. **PIER** (point-of-interest error rate) counts errors
only at the words the annotators tagged around each switch — a model that
transcribes the Korean perfectly and mangles every English word scores well on
MER and badly on PIER, and PIER is the one that predicts whether an action item
keyed on that word survives.

HiKE's loanword rule applies to both: `버그` and `bug` are the same word, and the
row's loanword list says which pairs. Without it the same predictions score MER
50.6 rather than 34.5, so any HiKE number quoted without the rule is a different
number.

The paper's baselines (Paik et al., EACL Findings 2026, arXiv 2509.24613): `whisper-large`
MER 26.1 / PIER 36.0; GPT-4o-transcribe 21.8 / 28.8; `whisper-medium` fine-tuned
on natural code-switched data 31.3 → 9.0. `large-v3` is not in the paper. This is
its first number on this benchmark.

## 2. Headline

| Run | MER | PIER | CER normalised | RTF |
| --- | --- | --- | --- | --- |
| `language="ko"` — production | 43.7 | 37.8 | 40.3 | 1.17 |
| Language detection — as HiKE ran it | **24.4** | **30.8** | 29.4 | 1.77 |
| Paper: `whisper-large` | 26.1 | 36.0 | — | — |
| Paper: GPT-4o-transcribe | 21.8 | 28.8 | — | — |

Read against the paper, `large-v3` with detection is 1.7 MER and 5.2 PIER points
better than the `whisper-large` the paper measured, and 2.6 / 2.0 behind
GPT-4o-transcribe. That is the expected order. The harness is measuring what the
paper measured.

Read against production, the 19-point MER gap between the two runs is the
finding, and section 3.1 is about where it comes from.

By code-switching level:

| Level | n | MER `ko` | MER detect | PIER `ko` | PIER detect |
| --- | --- | --- | --- | --- | --- |
| word | 457 | 68.4 | 27.5 | 49.6 | 34.6 |
| phrase | 607 | 27.8 | 23.1 | 30.1 | 28.7 |
| sentence | 57 | 15.3 | 14.1 | 24.4 | 23.2 |

By topic, the two that look like an Autune meeting:

| Topic | n | MER `ko` | MER detect | PIER `ko` | PIER detect |
| --- | --- | --- | --- | --- | --- |
| software development | 162 | 18.5 | 18.0 | 22.8 | 22.7 |
| business | 169 | 47.0 | 20.7 | 38.0 | 27.3 |

## 3. Findings

### 3.1 Pinning `ko` makes Whisper translate, not transcribe

HiKE is not a Korean corpus with English words in it. 491 of its 1,121 rows are
English sentences with a Korean word or phrase embedded — `the movie had great
visuals but it was missing that essential 감동`. Split the corpus along that
line and the language pin's cost is exactly located:

| Matrix language of the reference | n | MER `ko` | MER detect |
| --- | --- | --- | --- |
| Korean | 630 | 23.4 | 23.4 |
| English | 491 | **69.8** | 25.8 |

On the 630 Korean sentences the two runs are identical to the decimal. On the
491 English ones, `language="ko"` does not make the model mishear — it makes
the model **translate**. 208 of those 491 hypotheses contain no latin letters
at all; the reference `is there a good 국밥 restaurant near here` comes back as
`여기 근처에 좋은 국밥집이 있나요?`, fluent and wrong. Whisper's language token is
a decoding instruction, not a hint, and the model follows it.

This is why the word level looks catastrophic under `ko` (68.4) and the
sentence level does not (15.3): 262 of the 457 word-level rows are English
sentences with one Korean word, and 37 of the 57 sentence-level rows are English
sentences with a Korean clause — but a Korean clause is long enough for the
model to keep the language of each part.

Detection does not solve it, only most of it. 67 English-matrix rows are still
translated with detection on: a single Korean word early in a short clip is
enough for the 30-second detection window to say `ko`, and the rest of the
sentence follows.

### 3.2 What that means for the setting we ship

The pin was chosen, as the pipeline records, because detection on a short or noisy
opening picks the wrong language and the whole meeting comes back as nonsense.
Nothing here contradicts that for a Korean meeting: on Korean-matrix speech the
pin costs nothing, and the software-development topic — the closest thing in
HiKE to our meetings, 122 of its 162 rows Korean-matrix — scores the same either
way (18.5 vs 18.0).

What it adds is the failure mode's shape. A meeting that is mostly English with
Korean terms — a standup with a foreign teammate, a vendor call — will not be
transcribed badly under `ko`; it will be translated into Korean, and the
transcript will read as if the meeting had been held in Korean. Nothing
downstream can detect that, because the output is fluent.

The setting stays `ko`. The risk is recorded, and the cheap mitigation is
already in the pipeline's shape: `transcribe(language=None)` exists, and the
upload form could carry a "mostly English" switch that selects it. That is a
product decision, not a model one, and it goes to the roadmap rather than into
this branch.

### 3.3 The loanword rule is a third of the score

| Loanword-labelled rows (583) | MER with the rule | MER without |
| --- | --- | --- |
| `ko` | 34.5 | 50.6 |
| detect | 23.7 | 38.6 |

`프레젠테이션` for `presentation`, `파워포인트` for `powerpoint`: the model writes
the Korean spelling of an English word it heard, and half the corpus's rows
have at least one such word. HiKE treats either spelling as correct where the
annotators listed the pair, and so does this evaluation. The 16-point gap is a
statement about orthography, not recognition — the model heard the word.

The same logic was carried into our own term metric: `term_accuracy` now takes
`aliases`, and evaluation 01's `pyannote` → `파이어노트` remains a miss (that is a
different word) while `pipeline` → `파이프라인` would not be. Evaluation 01's
numbers were not re-scored; the rule applies from here.

The rule has a limit worth knowing. The annotators listed the pairs they
noticed, so `프레젠테이션` is forgiven on one row and counted on another
whose list omits it. HiKE's numbers, ours included, carry that noise.

### 3.4 Subtitle boilerplate is back, on short clips

Evaluation 01 found no hallucinated text over silence, on a recording with
real pauses. HiKE's clips are 2–25 seconds and end abruptly, and on 11 of them
— in both runs, the same 11 — the model appended `다음 영상에서 만나요` or a
`시청해 주셔서 감사합니다` after the sentence. There is no silence for it to be
hallucinated over; the clip simply ends, and the model finishes it the way its
training data finishes. On a meeting recording this shape does not occur, but
a live-capture chunk boundary has it, and the repetition detector — the only
hallucination guard in the pipeline — does not look for it.

### 3.5 RTF on a benchmark is not RTF on a meeting

| Run | RTF | Target |
| --- | --- | --- |
| `ko` | 1.17 | ≤ 1.5 |
| detect | 1.77 | ≤ 1.5 |

The clips average 7 seconds. Per-utterance overhead — the language detection
pass in particular, which runs on the first 30 seconds of every input however
short — is amortised over nothing, which is why detection costs 0.6 RTF here. On an
11-minute meeting file the same pass runs once. The `ko` number is the honest
one for this hardware; the detect number is an upper bound on short inputs and
says nothing about meetings.

## 4. What HiKE cannot measure

- **The glossary.** There is no meeting vocabulary to build a prompt from, so
  every number here describes the bare model. The prompt's effect — evaluation
  01's 32% term accuracy and what `hotwords` does to it — is still measured on
  the in-house recording and only there.
- **Diarization.** One speaker per utterance. No DER.
- **Masking.** No personal data. No recall.
- **Our own stack's names.** `pyannote`, `CTranslate2`, `DeBERTa` do not occur.
  HiKE's software-development rows say the model handles `API gateway
  migration`; they do not say it handles ours.
- **Long-form behaviour.** Context bleed, repetition collapse, the effect of
  `condition_on_previous_text` — all need minutes of audio, and no clip has
  them.

The two corpora therefore stay two corpora. HiKE answers one question,
comparably to the literature; the in-house recording answers the rest.

## 5. What this changes

1. **`large-v3` stays.** On the only external code-switching yardstick
   available it beats the paper's `whisper-large` on both metrics and sits
   within three points of GPT-4o-transcribe. There is no evidence here for a
   different checkpoint, and evaluation 01's finding stands: the remaining
   error on our recordings is vocabulary, which HiKE cannot see and a benchmark
   cannot fix.
2. **`language="ko"` stays, with a named risk.** Free on Korean speech; a
   translation, not a transcription, on English-matrix speech. An upload-time
   "mostly English" option is the mitigation and is a product decision.
3. **Loanwords in either script are correct**, in HiKE scoring and in
   `term_accuracy`. Decided 2026-09-16.
4. **The next comparison is a model, not a setting.** Qwen3-ASR (Alibaba,
   Apache-2.0, 1.7B / 0.6B) reports Fleurs Korean WER 2.57 and has never been
   measured on code-switching. The harness scores predictions made elsewhere
   (`--score-only`), so a GPU run of Qwen3-ASR over these 1,121 rows gives a
   comparable MER / PIER without touching production. The bar it has to clear
   is not this table's MER; it is CPU RTF ≤ 1.5, word timestamps for speaker
   alignment, and a way to inject the glossary — none of which it has been
   shown to have. That comparison is a separate branch.
5. **Fine-tuning is not on the six-week path.** The paper's 31.3 → 9.0 is
   real and needs a GPU training pipeline and a training set HiKE does not
   provide. It is the three-month roadmap's candidate if the glossary and
   correction work leave a gap that a benchmark can still see.

## 6. Reproducing this

```bash
# All 1,121 rows, production settings; resume with the same command after an interruption
uv run python modules/audio/scripts/evaluate_hike.py --predictions hike-large-v3-ko.jsonl --resume

# The same rows with language detection — the run comparable to the paper's table
uv run python modules/audio/scripts/evaluate_hike.py --predictions hike-large-v3-detect.jsonl \
    --language "" --resume

# Re-score without a model, e.g. after a scorer change or for predictions made elsewhere
uv run python modules/audio/scripts/evaluate_hike.py --score-only hike-large-v3-ko.jsonl
```

Each run writes `<predictions>.summary.json` with the tables above and the
run's settings. The parquet (235 MB) is fetched once into the Hugging Face
cache. On an M4 Pro, CPU only, the `ko` run took 2 h 35 min of transcription
time for 2.22 h of audio and the detect run 3 h 56 min; keep the lid open — the
process pauses in sleep, and `--resume` is what continues it afterwards.

Both prediction files and both summaries are outputs and are not committed.
The scorer's fidelity to HiKE is tested in
`modules/audio/tests/unit/test_eval_codeswitch_fidelity.py`, from a fixture
regenerated with `modules/audio/scripts/hike_fidelity_fixture.py`.
