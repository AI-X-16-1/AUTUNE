# Module A — build history

What was built, what it was measured at, and what each number changed. Written
for the next person who has to decide where this module's effort goes.

Evaluation reports live in `docs/modules/audio-evaluations/` and hold the full
tables. This file is the thread through them: the decisions, the reversals, and
what is still open.

Last updated: 2026-09-11.

---

## 1. The pipeline, as it stands

```
recording ──> decode ──> transcribe ──> diarize ──> assign speakers ──> mask PII
                │           (Whisper)   (pyannote)      (word-level)     (regex + rules)
                │                                                            │
           delete audio ◀───────────────────────────────────────────────────┘
                                                                             │
                                                        persist utterances ──┴──> publish TranscriptReady
```

| Stage | Module | Status |
| --- | --- | --- |
| Decode | `decoding.py` | merged (#81) |
| Transcribe | `pipeline.py`, `glossary.py` | merged (#81, #135) |
| Transcript quality guard | `quality.py` | merged (#133) |
| Raw-audio deletion | `storage.py` | merged (#117) |
| Diarize | `diarization.py` | merged (#136) |
| Assign speakers to words | `speakers.py` | merged (#136) |
| PII masking — patterns | `masking.py` + `autune_integrations.privacy` | open, PR #138 |
| PII masking — spoken numbers | `recognition.py` | open, PR #158 |
| Persist + publish | `persistence.py` | open, branch `audio/persist-utterances` |
| Event publishing | `autune_core.events` | merged (#145) |

Speaker **identification** (matching a voice to a person, #6) is not built. Every
`participants.user_id` is `NULL` today, and several downstream bugs are waiting
on that changing — see section 6.

---

## 2. What the measurements said

Two evaluations against one real 13-minute recording: five sessions read from a
cue sheet, four people, CPU only, no GPU.

### Evaluation 01 — transcription (`docs/modules/audio-evaluations/01-baseline-large-v3.md`)

| Session | Content | CER (normalised) | Target 5% |
| --- | --- | --- | --- |
| S1 | Identical sentence, four readings | **0.035** | pass |
| S2 | Domain terminology | **0.307** | fail, 6× over |
| S3 | Numbers, dates, amounts | **0.080** | near |
| S4 | Simulated meeting (ad-lib) | **0.158** | upper bound |
| S5 | Stress drills | **0.19–0.59** | per drill |

| | Result | Target |
| --- | --- | --- |
| Hallucinated characters over silence | **0** | 0 |
| Term accuracy | **10 / 31 = 32%** | ≥ 85% |
| Number and date accuracy | **42 / 43 = 98%** | 100% |
| RTF (11m37s file) | **0.73** | ≤ 0.3 |

**The finding that should drive the next two weeks.** S1 is 3.5% and S2 is 30.7%
on the *same speaker, same microphone, same room*. A tenfold difference with
every recording condition held constant is not an audio problem — it is
vocabulary. `large-v3` transcribes Korean speech well and has never heard of
`silero-VAD`, `tabCapture`, `DeBERTa` or `pgvector`.

Numbers and dates are nearly perfect (98%), which matters: module B parses due
dates out of this text.

### Evaluation 02 — diarization (`docs/modules/audio-evaluations/02-diarization.md`)

| | Result | Target |
| --- | --- | --- |
| Speakers found | **4**, correct | 4 |
| **DER** (S1) | **0.141** | ≤ 0.15 |
| Words preserved through the join | 945 → 945 | all |
| Diarization time (11m37s, CPU) | 377 s | — |

Two caveats that the number does not carry on its own:

- **The reference is hand-written** and only honest for S1, where four known
  people speak one at a time. There are no per-speaker tracks, so DER on
  overlapping speech is unmeasured.
- **A first attempt scored 0.209**, because the reference treated the silence
  between readings as speech and charged the diarizer for correctly finding it.
  The 0.141 is against a reference built from Whisper's segment times.

**DER is why we expect a voice to be split across two clusters — it is not a way
to detect that it happened in a given meeting.** Production has no reference.
That property has since broken two other modules (section 6).

---

## 3. Decisions, and the ones that reversed

### Glossary: `hotwords`, then `initial_prompt`, then `hotwords` (#135)

faster-whisper has two bias channels and they behave differently:

| | Where it lands | Lifetime |
| --- | --- | --- |
| `initial_prompt` | `previous_tokens`, truncated to the last 223 | **evicted by decoded text** |
| `hotwords` | re-prepended on every `get_prompt` call | survives the whole file |

Korean is roughly one token per syllable, so an `initial_prompt` survives about
30–40 seconds of transcript and then is gone.

**On the 165-second file, `initial_prompt` won.**

| | Drill 04 terms | Drill 04 CER |
| --- | --- | --- |
| baseline | 2/11 = 18% | 0.404 |
| `hotwords` | 4/11 = 36% | 0.347 |
| **`initial_prompt`** | **9/11 = 82%** | **0.155** |

**On the 11m37s file it loses to doing nothing.** Whole transcript against whole
reference, so segment-boundary drift cannot flatter any variant:

| | CER | Term accuracy |
| --- | --- | --- |
| baseline | **0.157** | 9/29 = 31% |
| `initial_prompt` | 0.232 | 8/29 = 28% |
| **`hotwords`** | 0.170 | **25/29 = 86%** |
| both | 0.220 | 15/29 = 52% |

`hotwords` costs 0.013 CER against the baseline and buys 55 points of term
accuracy. `initial_prompt` is **worse than no glossary at all** at meeting
length, and combining the two is worse than `hotwords` alone.

That reversal is the whole point: **a bias setting measured on a 165-second file
does not predict an 11-minute one.** On the short file the first technical term
arrives at 34 seconds, while the prompt is still there; on the long one it
arrives at 148 seconds, by which time decoded text has pushed it out.

A framing prefix on both channels was measured separately, and is a different
number from any of the above: 45% term accuracy without it, 62% with.

### Decoder repetition guard (#133)

Whisper can collapse into repeating one phrase for minutes. Thresholds came from
seven real runs, not from intuition:

```
MIN_DISTINCT_RATIO            = 0.5
MAX_REPEAT_RUN                = 10
MIN_SEGMENTS_TO_JUDGE         = 20    (gates only the ratio)
MIN_CHARS_TO_COUNT_AS_A_REPEAT = 6
```

On a collapse, the pipeline retries once with `condition_on_previous_text=False`
and **no bias at all** — the glossary is a plausible cause of a loop, so the
retry removes it.

### Which pyannote track the join consumes (#136)

pyannote 4.x returns `speaker_diarization` (contains overlaps) and
`exclusive_speaker_diarization` (does not). DER is scored against the first,
because that is what the metric is defined over. **The join consumes the
second**, because a word belongs to one speaker and reading overlapping turns
hands an interruption to whoever started talking first.

On this recording the choice changes almost nothing — and that is the point, not
a reassurance: S1–S3 are one person at a time, so this recording cannot measure
what the two tracks disagree about.

### PII masking is patterns, not a model (#138, #158)

`privacy.md` §2 names five categories: phone, email, national ID, bank account,
card. A Korean NER checkpoint was the obvious build and is the **wrong tool** —
a general inventory is person / place / organisation / date / time / quantity,
and none of the five appears in any published one.

The actual problem is a writing-system difference: `공일공` and `010` are one
number in two scripts. So `recognition.py` substitutes digit syllables and asks
the existing patterns again. No checkpoint, no GPU, no new dependency.

Each digit syllable is one character and becomes one digit character, so the
rewrite is **length-preserving** — a span found in the rewritten text is the same
span in the original, and the offset mapping that usually breaks this kind of
code does not exist.

---

## 4. What kept going wrong

The same failure shape appeared six times in three days, in different files and
by different hands. It is worth naming because the counter-measure is always the
same.

**The same fact written in two places, only one of which gets fixed.**

| | |
| --- | --- |
| PII patterns in the masker and in the outbound guard | #126 — the guard could not see the most ordinary shape in a Korean transcript |
| `MaskedText` regex in two features | #139 |
| A `CODEOWNERS` claim copied into a doc | #60 |
| The gated-repo count in two docs | #63 / #69 |
| The `GapReport` id space in a doc and a fixture | #166 |
| `TERMINAL_EVENTS` in a test and (needed) in production | #170 |

The counter-measure that works is not discipline. It is **making the copy
impossible or making the drift fail loudly in CI** — one builder for a Slack
body; one `PII_PATTERNS`; a test that reads `types.ts` and compares it with the
Python schema.

**And the harder version: a rule written in a comment is not a rule.** Three
times in one day I wrote the rule down and broke it in the same file.

- `/** Per kind, per meeting. Never per person — privacy.md section 3. */` sat
  two functions below one that counted utterances per person (#140).
- A comment said the account pattern's second group takes six to eight digits;
  the regex said seven to eight, and the case the comment claimed to cover leaked
  (#138).
- A docstring said the scale-word rule was tested; the test could not have
  failed (#158).

**And the version that costs the most: a test that checks less than it claims.**

- A 24-digit regression row passed because the pattern it tested could not reach
  24 digits, while the rule it was protecting was broken.
- `test_the_number_itself_does_not_survive` asserted the *spoken* half had gone
  and passed on output that kept every digit of the number.
- The id false-positive test ran at 200 samples; the remaining bug appeared at
  roughly 1 in 10,000, and was found at 33,000.
- A terminal-event test asserted the return value, which the fix did not change.

Each one was found by **deliberately breaking the code and checking that the
test failed**. That step is now part of how these land.

---

## 5. Privacy, as code

`privacy.md` is a document; invariant 11 says the rules have to be constraints.
Where they are:

| Rule | Where it is enforced |
| --- | --- |
| Raw audio deleted after transcription | `storage.py` — a recording exists only inside a `with`; deletion in `finally`, `deleted` read back from the filesystem |
| Audio never written somewhere it survives | `storage.py::_reject_persistent` — refuses a temp dir inside a cloud-sync folder or the checkout |
| Text masked before the first write | `persistence.py` verifies with `mask()` **before** the first delete — **branch `audio/persist-utterances`, not merged** |
| Nothing unmasked leaves | `check_outbound` runs on every outbound channel, but the patterns it runs are **still the broken ones on `main`**: #126 (070 · 080 · 0505 · international) and #131 (a Korean particle ends the match) are both open. PR #138 closes #126; #131 has no PR yet |
| No per-person speech volume | `LiveTranscript` lists unnamed voices instead of counting them (#140) |

Two of those exist because a review found the gap, not because the rule was
followed: the masker and the guard disagree about what personal data is (#126,
open — the fix is in #138), and S13 printed a per-voice utterance count in the
same PR whose body said it did not (#140).

**The second column is where the rule is enforced, not proof that it is.** Two
of the five rows are on branches. A guarantee that has not merged is a guarantee
nobody has.

---

## 6. What is open, and why it matters

### Blocked on speaker identification (#6)

`participants` holds **one row per diarization label**, and splitting one voice
into two clusters is diarization's characteristic failure. Once `user_id` is
filled, one person can own several rows in one meeting. That property has already
broken two other modules and each fixed it locally:

- **E (#128)** — one person counted as two passed the small-meeting gate on
  speaking ratio and let a co-attendee derive the other person's exact share as
  `1 - own`.
- **C (#164)** — a split person was reported as having spoken on a topic *and*
  been silent on it; a participation gap raised on that silence is a false
  statement about somebody who spoke.

Both are latent today because `user_id` is always `NULL`. **They go live the day
#6 ships.** The rule is now written down once in
`docs/architecture/data-model.md`, "A participant row is a voice, not a person".

`TranscriptMetadata.participants` still has no description in the contract, and
the same trap reaches B and C through the payload rather than the table. Mine to
fix.

### Detector gaps

| | |
| --- | --- |
| #143 | A national ID whose first group is mis-transcribed matches nothing |
| #148 | Two six-digit figures separated by a space read as a national ID |
| #160 | Numbers read with commas, fillers, or one syllable at a time |
| #162 | Separators wider than one character, en dash, parentheses |

All four are the same boundary question, pulled in opposite directions — widening
one narrows another. **They should be measured together once an evaluation set
exists, not fixed one at a time.**

### Decisions waiting on other people

| | |
| --- | --- |
| #92 | Legal review of ADR 0007 — Q4 gates whether embedding collection needs separate consent, which gates #6 |
| #155 | S13's spec asks for live classification counts; the architecture deliberately has no path to fill them |
| #106 | No frontend test infrastructure — the S13 components have no component tests |

---

## 7. Where the effort should go

Ordered by what the measurements say, not by what is pleasant.

1. **Vocabulary.** S2 at 30.7% against a 5% target is the largest single gap, and
   `hotwords` already moved term accuracy from 34% to 86% on one drill. A
   per-meeting glossary that is actually populated — from the team's past
   meetings, their Notion, their repo — is the highest-value work in this module.
   The mechanism exists (#135); the source of terms does not.

2. **Speed.** RTF 0.73 against a 0.3 target, CPU int8. Options in order of
   expected return: GPU; `large-v3-turbo` (unmeasured); batching. The target is
   1.5× recording length end to end (`docs/modules/audio.md`); measured,
   transcription is 0.73 and diarization adds 0.54 on top, so this is not only
   Whisper.

3. **An evaluation set.** Every threshold in this module is a placeholder chosen
   from one recording: the masking thresholds, the repetition guard, the glossary
   mode. Four open detector issues cannot be resolved without one, and the
   `#61` target argument is about what a number means under which conditions.

4. **Speaker identification (#6)**, once #92 answers. It closes two latent bugs in
   other modules as a side effect.

5. **Overlapping speech.** DER is measured on one-speaker-at-a-time audio. The
   next recording needs per-speaker tracks — that is the case the two pyannote
   tracks disagree about and the case a real meeting is full of.

---

## 8. Reading order

| To understand | Read |
| --- | --- |
| What the module does | `docs/modules/audio.md` |
| The rules that break other people | `/CLAUDE.md`, `modules/audio/CLAUDE.md` |
| Transcription numbers | `docs/modules/audio-evaluations/01-baseline-large-v3.md` |
| Diarization numbers | `docs/modules/audio-evaluations/02-diarization.md` |
| Why privacy is written the way it is | `docs/architecture/privacy.md` |
| What a participant row means | `docs/architecture/data-model.md` |
