# Evaluation 01 — `large-v3` on a real 13-minute meeting recording

**Date:** 2026-09-09 · **Owner:** 김민경 · **Module:** A, Audio Pipeline

The first time module A's pipeline was pointed at audio that a person actually
spoke rather than at a fixture. It establishes the baseline every later change is
measured against, and it answers one question: *is the transcription good enough
for B, C, and D to build on?*

The short answer is that the model is good enough and the vocabulary is not.

---

## 1. What was measured

| | |
| --- | --- |
| **Recording** | Two files, 13 minutes total, produced from the module A cue sheet |
| **Sessions** | S1 calibration · S2 domain terms · S3 numbers and dates · S4 simulated meeting · S5 stress drills |
| **Model** | `large-v3`, faster-whisper 1.2.1, CTranslate2 int8 |
| **Hardware** | CPU only, no GPU |
| **Settings** | `beam_size=5`, `word_timestamps=True`, `vad_filter=True`, `language="ko"` |
| **Scored by** | `autune_audio.eval.korean` |

The recording was made by one person reading all four parts, so speaker
diarization is out of scope here: there is no per-speaker ground truth and
therefore no DER. Everything else the cue sheet asks for was measured.

## 2. Headline

| Session | Content | CER raw | CER normalised | Target 5% |
| --- | --- | --- | --- | --- |
| S1 | Identical sentence, four readings | 0.107 | **0.035** | pass |
| S2 | Domain terminology | 0.309 | **0.307** | fail, 6× over |
| S3 | Numbers, dates, amounts | 0.104 | **0.080** | near |
| S4 | Simulated meeting (ad-lib, upper bound) | 0.181 | **0.158** | upper bound |
| S5 | Stress drills | — | 0.19–0.59 | per drill |

| Metric | Result | Target |
| --- | --- | --- |
| Hallucinated characters over silence | **0** | 0 |
| Term accuracy | **10 / 31 = 32%** | ≥ 85% |
| Number and date accuracy | **42 / 43 = 98%** | 100% |
| RTF, 11m37s file | **0.73** | ≤ 0.3 |
| RTF, 2m45s file | **1.19** | ≤ 0.3 |

RTF differs between the two files because model loading is a fixed cost that a
short file cannot amortise. Neither number is a GPU number; both are CPU int8.

## 3. Findings

### 3.1 The model is fine. The vocabulary is not.

This is the finding that should drive the next two weeks of work.

```
S1  CER 3.5%    same speaker, same microphone, same room
S2  CER 30.7%   same speaker, same microphone, same room
```

The only difference between the two sessions is that S2 contains English proper
nouns. Recording quality, speaker, and noise floor are identical. A tenfold CER
gap across that single variable means the 30% is a lexicon problem, not an
acoustic one — and lexicon problems are attackable.

What the model produced for our own stack:

| Expected | Heard as |
| --- | --- |
| `pyannote` | 파이노트 · 파이어노트 · 하이에노트 |
| `faster-whisper` | 페이스터 위시퍼 · 위스포 |
| `CTranslate2` | 시트랜슬레이트 투 · CE Translator 2 |
| `ECAPA-TDNN` | EC-KAPA-TDN · 이스카파 TDNN |
| `silero-VAD` | 슬로우 VAD · 실로 VED · 쉘로 VA 뒤로 |
| `Neo4j` | 네오포젯제이 |
| `spaCy NER` | 스페이시 넬로 |
| `Sentence-BERT` | 센트베트 인베이딩 |
| `DeBERTa` | 데벨타 |
| `SetFit` | Cepid |
| `FastAPI` | PEST API · 패스트 API |

The ten terms that survived are all either capitalised acronyms (`RTF`, `CER`,
`BM25`) or product names common enough to be in the training data (`XGBoost`,
`PostgreSQL`, `Tailwind`). Not one of the libraries this project is actually
built on came through intact.

This is not a cosmetic problem. Module B keys action-item extraction on entity
names; `pyannote` heard as `파이노트` is a different entity, and the action item
attached to it is lost.

### 3.2 Confidence does not find these errors

The obvious fix — flag low-confidence words and correct those — was tested and
does not work.

| Threshold | Recall on term errors | Words flagged | Precision |
| --- | --- | --- | --- |
| 0.5 | 12% | 5.8% | 7% |
| 0.6 | 28% | 8.5% | 11% |
| 0.7 | 34% | 11.4% | 10% |
| 0.8 | 53% | 18.0% | 10% |

Median confidence is 0.774 on misrecognised terms against 0.980 elsewhere, so
the signal exists — but it is nowhere near separable. Whisper is *confidently
wrong*: it does not know that `파이노트` is a mistake, because as a Korean string
it is perfectly plausible. Nothing that keys on confidence will work here.
Correction has to be driven by a lexicon, not by uncertainty.

### 3.3 Two thirds of S1's error was spelling, not recognition

```
S1  raw 0.107  →  normalised 0.035
```

The model writes `30cm` where the speaker said `삼십 센티미터`, and `16kHz` where
they said `십육 킬로헤르츠`. Both spellings are correct Korean; scoring them apart
measures orthography rather than recognition. Mapping spoken units to their latin
form — now part of `normalise()` — cut S1's CER by two thirds.

The gap between the raw and the normalised number is exactly what post-processing
can recover. What remains, 3.5%, is the model.

### 3.4 Silence is already safe

25 seconds of silence and non-speech noise produced **zero characters**. The
subtitle boilerplate that Whisper is known for over silence never appeared. The
current `vad_filter=True` is sufficient, and the planned work on
`condition_on_previous_text` for hallucination control is not needed.

### 3.5 But previous context does bleed, and quiet audio is where it wins

Two cases, both real:

- S5 drill 06, spoken from 2 m away: `다음 회의는 9월 18일…` came out as
  **`TAM은` 9월 18일…**. The preceding drill was the TAM / SAM / SOM paragraph.
- S5 drill 05, spoken quietly: `SOM은 1,500만 달러` became `SAM은 1500달러` —
  `SAM` repeated three times in a row.

The same TAM / SAM / SOM sentence read at normal volume in S3 transcribed
correctly. So the failure is conditional: previous context overrides the acoustic
signal only when that signal is weak. In a real meeting the quiet speaker and the
far-from-microphone speaker are the same person, and they are the one whose
words get overwritten by whatever the loud speaker just said.

### 3.6 One action item's deadline was destroyed

S4 planted five action items and two decisions for module B to extract. Four
action items, both decisions, the unresolved question, the concern, the vague
agreement, and the self-correction all survived transcription intact — including
`화요일에— 아니 아니, 수요일에`, which is exactly the kind of thing B needs to
handle.

One did not:

> Reference: `다음 주 **수요일**까지 하겠습니다`
> Transcript: `다음 주 **10월**까지 하겠습니다`

A weekday became a month. B would create an issue with a deadline five weeks
wrong, and nothing downstream could detect it — the sentence is grammatical and
the transcript carries no signal that anything went missing.

Numbers and dates were otherwise near-perfect (42/43). The one other failure was
`ARPU 144달러` heard as `AIPU 99달러 … 1144달러`, in the same quiet passage as
3.5.

## 4. Experiment: does a glossary fix it?

Run on the same audio, changing one thing at a time. `faster-whisper` offers two
ways to bias the vocabulary and they are not equivalent — the difference only
shows at meeting length.

### On the 165-second file, `initial_prompt` won

| | Drill 04 terms | Drill 04 CER | Hallucination |
| --- | --- | --- | --- |
| baseline | 2/11 = 18% | 0.404 | 0 |
| `hotwords` | 4/11 = 36% | 0.347 | 0 |
| `initial_prompt` | 9/11 = 82% | 0.155 | 0 |

### On the 11m37s file, it loses to doing nothing

Whole transcript against whole reference, so segment-boundary drift cannot
flatter any variant:

| | CER | Term accuracy |
| --- | --- | --- |
| baseline | **0.157** | 9/29 = 31% |
| `initial_prompt` | 0.232 | 8/29 = 28% |
| **`hotwords`** | 0.170 | **25/29 = 86%** |
| both | 0.220 | 15/29 = 52% |

`hotwords` clears the 85% target. `initial_prompt` is worse than no glossary at
all, and combining them is worse than `hotwords` alone.

### Why the reading reversed

`faster_whisper`'s `get_prompt` treats the two channels differently:

```python
hotwords_tokens[: self.max_length // 2 - 1]        # head kept, re-applied every segment
previous_tokens[-(self.max_length // 2 - 1) :]     # tail kept — initial_prompt lives here
```

`initial_prompt` is appended to `previous_tokens`, a 223-token window that
decoded text keeps pushing into. Korean runs about a token per syllable, so the
glossary survives roughly 30 to 40 seconds of transcript and is then evicted.

The two recordings differ in exactly that: the short one says its first
technical term at 34 seconds, while the long one says it at 148. **On the short
file the glossary was still in the window; on the long one it was gone before a
single term was spoken.** `hotwords` are re-prepended on every `get_prompt`
call, so they reach the end of a meeting.

The first reading was not wrong about the file it was taken on. It was wrong to
generalise from it, and a 45-minute meeting is the case this pipeline is for.

### What it costs and what it buys

`hotwords` moves CER from 0.157 to 0.170 — 0.013 worse — while term accuracy
goes from 31% to 86%. That is the trade this is for: a wrong particle costs
readability, a wrong entity name costs the action item attached to it.

Sixteen terms came back and none were lost: `pyannote`, `faster-whisper`,
`silero-VAD`, `tabCapture`, `large-v3-turbo`, `DeBERTa`, `spaCy`, `NER`,
`Neo4j`, `PageRank`, `betweenness`, `cross-encoder`, `SetFit`, `Prophet`,
`FastAPI`, `Bolt for Python`.

Four still fail, and they are the compound ones: `speaker embedding`,
`ECAPA-TDNN`, `CTranslate2`, `Sentence-BERT`. Those are the workload for the
correction pass in 5.2.

## 5. What this changes

| Priority | Action | Why |
| --- | --- | --- |
| 1 | Build the glossary into `hotwords`, per meeting | 4 — measured, 31% -> 86% at meeting length |
| 2 | Lexicon-based correction pass over the transcript | 3.2, 4 — what the prompt misses, confidence cannot find |
| 3 | Seed the glossary from `participants` and `aud_corrections` | 3.6 — two of four names were wrong, and B keys assignees on them |
| 4 | Keep `vad_filter=True`; drop the planned `condition_on_previous_text` work | 3.4 — already solved |
| 5 | Loudness-normalise before transcription | 3.5 — context bleed is a symptom of weak signal |
| 6 | GPU, or measure `large-v3-turbo` | RTF 0.73 against a 0.3 target |

### 5.1 The glossary is per meeting, not global

Both channels are capped at 223 tokens. A company-wide vocabulary does not fit
and would dilute what does. The glossary has to be assembled for each meeting
from sources module A already owns: the meeting's `participants`, a small static
list of the project's stack, and `aud_corrections`.

`aud_corrections` closes a loop entirely inside module A: a user fixes 파이노트 to
`pyannote` in the transcript UI, the correction is stored, and the next meeting's
prompt contains it. No module boundary is crossed and the system improves with
use. Team-wide vocabulary from past meetings is module D's data; if it is ever
wanted here it arrives as a contract, never as an import.

### 5.2 Correction has to be local, and that is a constraint, not a preference

Term correction must run *before* PII masking, so the masker sees correct text.
Invariant 11 says unmasked transcript text reaches no external service. Therefore
the corrector cannot be an LLM API call — it has to be a local, deterministic
algorithm.

Phonetic matching fits, and the errors invite it: they are phonetically faithful,
which is why confidence cannot see them (3.2) and why edit distance can.

```
파이노트  -> romanise -> painoteu -> drop epenthetic 으 -> painot
pyannote                                                 -> pyanot     distance 1
데벨타    -> debelta  -> collapse ㄹ to {r,l}            -> debert
DeBERTa                                                  -> debert     distance 0
스페이시  -> seupeisi                                    -> speisi
spaCy                                                    -> speisi     distance 0
```

Confusion sets to collapse: ㄹ/{r,l}, ㅂ/{b,p,v,f}, ㅈ/{j,z}, ㅅ/{s,th}. False
positives stay controlled because the candidate set is the meeting's glossary —
tens of terms, not a language.

Diarization, speaker attribution, and DER remain unmeasured. They need a
recording with per-speaker tracks, which this one does not have.

## 6. Reproducing this

```bash
# Serve the manual test page (local only)
uv run uvicorn autune_api.main:app --reload
# then open http://localhost:8000/api/audio/dev

# Or score structures directly
uv run --package autune-audio pytest modules/audio/tests/unit/test_eval_korean.py
```

Recordings are not in the repository: they are meeting audio, and invariant 11
applies to our own voices too. They live with the module owner.
