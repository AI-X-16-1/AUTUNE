# Module A — build history

What was built, what it was measured at, and what each number changed. Written
for the next person who has to decide where this module's effort goes.

Evaluation reports live in `docs/modules/audio-evaluations/` and hold the full
tables. This file is the thread through them: the decisions, the reversals, and
what is still open.

Last updated: 2026-09-22.

---

## 1. The pipeline, as it stands

```
upload ──> stage file ──> open meeting ──> queue task
                                               │
                                               ▼
recording ──> decode ──> transcribe ──> diarize ──> delete audio
                         (Whisper)     (pyannote)        │
                                                         ▼
                                            assign speakers ──> mask PII
                                              (word-level)    (regex + rules)
                                                                     │
                                       persist utterances ◀──────────┘
                                                │
                                                └──> publish TranscriptReady
```

Everything above the deletion needs the audio; nothing below it does. The two
halves are separated deliberately — see section 3.

| Stage | Module | Status |
| --- | --- | --- |
| Accept an upload, open a meeting, queue the task | `router.py`, `service.py`, `storage.py` | open, PR #209 |
| Decode | `decoding.py` | merged (#81) |
| Transcribe | `pipeline.py`, `glossary.py` | merged (#81, #135) |
| Transcript quality guard | `quality.py` | merged (#133) |
| Raw-audio deletion | `storage.py` | merged (#117) |
| Diarize | `diarization.py` | merged (#136) |
| Assign speakers to words | `speakers.py` | merged (#136) |
| PII masking — patterns | `masking.py` + `autune_integrations.privacy` | merged (#138) |
| PII masking — spoken numbers | `recognition.py` | open, PR #158 |
| Persist + publish | `persistence.py`, `tasks.py` | merged (#184) |
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
in *one recording session, one room, one microphone*. A tenfold difference with
the acoustic conditions held constant is not an audio problem — it is
vocabulary. `large-v3` transcribes Korean speech well and has never heard of
`silero-VAD`, `tabCapture`, `DeBERTa` or `pgvector`.

The report itself says "same speaker" here, and that phrase is left over from a
draft where one person read all four roles. #132 corrected the roles and did not
correct this line, so eval-01 now contradicts its own header, its own
four-speaker CER table and its own DER section. The comparison survives — every
voice in S2 also read S1, and S1's per-speaker CER spans 0.024 to 0.049, nowhere
near S2's 0.307 — but the sentence does not, and this file does not repeat it.
#181 fixes it at the source; until that merges, eval-01 still carries the
sentence this paragraph is warning about.

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

On the live-microphone runs of 2026-09-22 (a Mac microphone with almost
nothing above 1 kHz) one person came back as four speakers, then two. The
release valve is the one input pyannote's clustering cannot argue with: a
speaker count. `AUTUNE_AUDIO_DIARIZATION_NUM_SPEAKERS` (or min/max bounds)
reaches the pipeline call; unset, nothing changes. A per-meeting count
belongs with S10's attendee list (#325).

### Live channel — per-row lag (`docs/modules/audio-live-transcription.md` §9)

One synthetic run against the real WebSocket route with `large-v3` on an
Apple-silicon CPU: a 46.6 s two-speaker recording streamed as PCM16 at
real-time pace.

| Measure | Value |
| --- | --- |
| Rows | 5 of 5 utterances, one phone number masked, `speaker_id` null |
| Lag from utterance end to row | **7–10 s** (each ~10 s utterance takes ~9 s to transcribe) |
| Same recording faster than real time | wall ≈ audio length: the transcriber is the bottleneck, RTF ≈ 0.95 |
| Log | counts and ids only; no text, no traceback |

**The lag is one utterance, not a fixed delay, and nothing is dropped.** When
transcription falls behind, frames queue at the socket and the lag grows for
the rest of the meeting. Two live meetings on one process therefore do not
"double the delay" — past RTF 1 the delay stops converging. A bounded
segment queue with drop-oldest is the follow-up; the browser microphone path
(worklet, `MediaRecorder`) has not been measured by anyone yet.

**The live path now has its own model, thread count, and beam width.**
Isolated decode time for one 11.4 s Korean utterance, int8, beam 1, on a
14-core Apple-silicon CPU:

| model | 4 threads | 10 threads | Korean quality |
| --- | --- | --- | --- |
| large-v3 | 6.2 s | 4.6 s | reference |
| large-v3-turbo | 4.3 s | **2.4 s** | practically the same (both miss the same domain word) |
| small | 1.0 s | 0.8 s | unusable |

A 5.4 s utterance costs almost the same (turbo/10: 2.2 s) — the encoder pads
every segment to a 30 s window, so there is a ~2 s floor regardless of
utterance length. Decision: the live path gets `large-v3-turbo`
(`live_whisper_model`), its own thread count (`live_cpu_threads`), and beam 5
(`live_beam_size` — width 5 costs turbo 0.3 s more than width 1, 2.7 s vs 2.4 s); the stored path (`pipeline.transcribe`, worker) keeps
`large-v3`, beam 5, and its retry. Models are cached per `(name, threads)`,
so the two paths each build one instance and neither reloads.

Re-measured against the real route, same 46.6 s recording, same real-time
pacing, `large-v3-turbo` with 10 threads and beam 1: per-row lag went from
9.5 / 8.6 / 8.6 / 8.8 / 9.4 s (`large-v3`, 4 threads, beam 5) to
4.1 / 4.0 / 4.1 / 4.1 / 4.6 s. The end-to-end number stays above the 2.4 s
isolated-decode figure because it also carries the segmenter's 0.7 s silence
wait, VAD, masking, and the socket round trip.

**The live engine is chosen per machine (`live/backends.py`).** CTranslate2
has no Metal backend, so on a Mac the live path was stuck on the CPU floor
above. `mlx-whisper` runs the same turbo weights on Apple silicon's GPU:
isolated decode of the 11.4 s utterance 0.85 s (0.81 s for 5.4 s), text
identical to CTranslate2's, and inside the API — `live_decode` in the log —
0.82–0.87 s per row. Utterance end to row, real-time pacing, is now about
1.7 s (0.7 s silence wait + decode), against ~4 s on ten CPU threads.
`AUTUNE_AUDIO_LIVE_TRANSCRIBER_IMPL=auto` picks `mlx` where the optional
`mlx` extra is installed and can run, and `faster_whisper` everywhere else —
which on a machine with an NVIDIA GPU means `AUTUNE_AUDIO_DEVICE=cuda`, the
setting the stored path already had. Two facts to know when reading the
client's own clock: it starts before `hello`, so `ready` (warm-up, 2–7 s)
has to be subtracted from every row time; and the glossary goes to
mlx-whisper as `initial_prompt`, which is what makes it write "검색 개편"
where the unprompted model wrote "검색해변".

**The first real-microphone runs, and what they taught.** Through the real
page the rows came out as fragments Whisper had guessed at ("Logic
감사합니다", "hovah 감사합니다", at confidence 0.1–0.25) between correct rows.
Two facts, both measured on a capture of the person's own audio: the Mac's
microphones (built-in and a wired earphone alike) deliver speech with almost
nothing above 1 kHz, which Whisper reads fine with a whole file and badly in
fragments; and the segmenter was scoring each 200 ms frame with a VAD that
had no memory of the frame before, so soft syllables read as silence and
sentences were cut into 0.4–1 s pieces. Three changes, replayed against the
same capture: the VAD now scores each frame at the end of a 0.6 s window
(`VAD_CONTEXT_S`); a row whose mean word probability is below
`live_min_confidence` (0.35) is not sent — the stored path remakes it; and
the mlx decode runs at temperature 0 with no fallback retries. Result on
that capture: 9 rows with 3 invented ones → 7 rows, none invented, the
remaining errors being the microphone's. Chrome's capture path was checked
separately and passes 1.5–5 kHz flat, so the muffling is upstream of the
browser.

### Live speaker labels (`docs/modules/audio-live-speakers.md`)

The live channel shipped with no speaker on a row (#307). This adds one:
one `pyannote/wespeaker-voxceleb-resnet34-LM` embedding per utterance -- the model
already inside `pyannote/speaker-diarization-3.1`, so no new download and the same
vector space #6 will identify against -- and nearest-centroid clustering in
the session with one cosine threshold. Labels are `화자 N` in order of first
appearance and never change once shown; the stored path was changed to say
the same thing (it had been showing pyannote's `SPEAKER_02` raw). A meeting
stored before this change keeps its `SPEAKER_00`-style participant rows; a
re-upload creates `화자 N` rows beside them (`persistence.py` already
documents that reruns cannot preserve the mapping), and no migration is
needed.

| Measure | Value |
| --- | --- |
| Embedder load | 0.4 s |
| Embedding per utterance, CPU (M4 Pro) | 12 ms at 0.5 s and 1 s, 19 ms at 3 s, 55 ms at 10 s |
| Threshold default | **0.55, provisional** -- the wespeaker convention until the sweep in `evaluate_live_speakers.py` has run on the four-speaker recording of evaluation 02 |

What the threshold sweep reports, and the value it settles on, goes in
`docs/modules/audio-evaluations/04-live-speakers.md` when the owner has run
it; this entry is updated then.

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

**That pair has no evaluation report behind it.** It is recorded in two code
docstrings (`glossary.py`, `test_glossary.py`) and nowhere else, so the terms,
the mode and the method it was taken under are not written down — and it is not
reconcilable with the 86% above without them. Treat it as a note, not as a
measurement, until it is re-run into `docs/modules/audio-evaluations/`.

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

### Three syllables was a claim about numbers, and the claim was false (#158)

`MIN_SPOKEN_SYLLABLES = 3` was written as "a switch of script never happens for
one syllable". 박재경 found two transcriptions where it does, and the second is
the one worth remembering:

```
010-1234-56칠팔   nine digits, no pattern matched, nothing was masked
010 1234 567팔    ten digits, read as an account, so the rule kept 4567
```

The second is masked, `counts` records a masked span, and four digits of a phone
number are standing in the output — **a leak that looks like a success from every
direction except reading it.**

Lowering the threshold to one brings back `버전 20260910 이사 갑니다`, measured.
The distinction that does hold is not the count but the **separator**: Korean
writes a number's groups without internal spaces and writes the next word with
one. So the threshold now applies only across a separator, and one syllable is
enough when it is written hard against a digit. `MIN_RUN_DIGITS` is unchanged and
is what still keeps `10일 이사` and `2사분기` out.

The residue is **not** in this file: spell the tail off on its own and the
patterns read `010 1234 567` as a ten-digit account and keep its last four — the
same output as input with no syllable in it at all. That is `account`'s
last-four rule, filed as #182.

### The pipeline deletes the audio before it masks (#184)

`docs/modules/audio.md` lists masking (step 6) before deletion (step 7). The
task does the opposite, and the reason is that **nothing after transcription and
diarization needs the audio**. Masking, the speaker join, the write and the
publish all run on text. Holding the recording across those steps buys nothing
and costs exactly the window invariant 11 exists to close.

The write also has to be after deletion for a second reason:
`PrivacyFlags.original_audio_deleted` is read from `Recording.deleted`, which is
read from the filesystem rather than from having reached a line. Written inside
the `adopt` block, the flag is still False — and a False flag is one every
consumer refuses on. The order is asserted rather than described: the test
records whether the file still exists at the moment the session opens.

### The event is built from the rows, not from the transcript (#184)

The obvious implementation assembles `TranscriptReady` from what the pipeline
just computed. It is wrong for one specific reason, and it is the kind that
would have shipped quietly:

**`speaker_id` and `role` live on `Participant`, and those rows are reused
across runs.** A `user_id` somebody confirmed from an earlier meeting's DM is in
the database and was never in this pipeline's output. Built from the transcript,
the event publishes `speaker_id=None` for a person the system already knows —
and every consumer treats null as "diarized but not identified".

Reading it back from the committed rows also makes the event a statement about
what is *stored*. Publish after the commit and the two cannot disagree.

`metadata.participants` had to be decided here because the contract field has no
description and **no module reads it** — D's fixture puts a speaker label there,
B omits it. A fills it with speaker labels, one per diarization label. It is not
a list of people: one person split across two clusters is two entries, which is
the property that broke E (#128) and C (#164). Filed as #183 so four consumers
agree on it rather than inheriting whatever A needed first.

### The worker is told the job, never the path (#259, #275)

The first upload endpoint handed the worker a path: `send_task(name,
args=[meeting_id, upload_path])`. `privacy.md` section 1 forbids exactly that
sentence — "passing a path to raw audio in a Celery payload" — and the PR
argued around it in `docs/modules/audio.md` instead of raising it, which
CLAUDE.md section 6 says not to do. The review caught it (@PARKJAEKYUNG0525),
and the reason it matters is not the log line module A controls: Celery writes
task arguments to the broker message and to its own failure output, so a path
in the payload is a path in two stores nobody in this module can scrub.

The fix is a table this module was always supposed to have. `aud_jobs` holds
one row per *attempt*; the file is renamed to `{job_id}.upload` at the claim,
the queue carries the id, and the worker asks `storage.upload_path` where that
is. The client's extension is not kept — ffmpeg probes the container from the
bytes, checked by decoding an `.m4a` renamed to `.upload` — which also closes
the NAME_MAX crash from #209's review without a regex.

**Per attempt, not per meeting, because of a race @lsh2217 named.** A `failed`
meeting accepts another recording. With one filename per meeting, a first
attempt turning up late would adopt the second attempt's file, and when it died
`mark_failed` would fail the meeting the second attempt was busy with. With one
row per attempt the earlier one is `superseded` and the worker declines it at
the door; `mark_failed` is keyed on the job and a superseded job cannot touch
the meeting. The same door declines a redelivery of a `done` job (the
`acks_late` case from #259's third fix) and, separately, one whose first
delivery is still `running` — that one must not delete the file, which the
first cut of this code did.

The sweep #209 had was dropped because it decided on mtime and could delete a
file a late task was about to adopt. It is back, deciding against `aud_jobs`
instead: an attempt that is over, or a job unknown to the database, has no
owner; a live job is left alone until `orphan_after_hours`. It runs at the
start of every `process_recording` until there is a periodic trigger (#207).

`privacy.md` section 1 was rewritten in the same PR (decision #275). "Scoped
to the task" never described a two-process handover; "owned by exactly one
party at a time" does, and names the two primitives.

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
| Text masked before the first write | `tasks.py` masks between diarization and the session; `persistence.py` verifies with `mask()` **before** the first delete and refuses — #184, merged |
| Nothing unmasked leaves | `check_outbound` runs on every outbound channel. #126 (070 · 080 · 0505 · international) and #131 (a Korean particle ended the match) are both closed by #138, merged — the patterns now end on a class that a Korean syllable is not in, so `010-1234-5678로` matches |
| No per-person speech volume | `LiveTranscript` lists unnamed voices instead of counting them — #140, merged |

Two of those exist because a review found the gap, not because the rule was
followed: the masker and the guard disagreed about what personal data is (#126,
closed by #138), and S13 printed a per-voice utterance count in the same PR whose
body said it did not (#140).

**The second column is where the rule is enforced, not proof that it is.** When
this section was first written three of the five rows pointed at branches, and
the sentence here was "a guarantee that has not merged is a guarantee nobody
has". All five are on `main` now — but the sentence is kept because the table
went stale in both directions within a day, and a reader who trusts it without
checking `main` is making the same mistake either way.

**#131 is still open on the tracker and is not a live bug.** #138 fixed it along
with #126 and closed only #126. The paragraph above said otherwise until the
patterns were actually run: every case in #131's own reproduction is caught on
`main` today. An issue's state is a claim about the tracker, not about the code,
and reading one as the other is the failure this section is about.

The masking row is two enforcements, not one, and the split is deliberate. The
task masks; `persistence.py` re-checks and refuses. A guard that is also the only
masker fails closed on every real meeting, which is how a privacy check gets
removed — so the check and the doing are separate, and the check uses the same
patterns that did the masking so the two cannot disagree (#126, from the other
side).

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
#6 ships.** #167 writes the rule down once, in
`docs/architecture/data-model.md` under "A participant row is a voice, not a
person" — **open, not merged**, so until it lands the rule is still two local
fixes and no statement.

`TranscriptMetadata.participants` still has no description in the contract, and
the same trap reaches B and C through the payload rather than the table. **#184
had to pick a meaning to ship** — speaker labels, one per diarization label — and
#183 asks the four consumers to agree on it rather than inherit whatever A needed
first. Nothing reads the field today, which is the only reason the choice was
still free.

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
   `hotwords` already moved term accuracy from 31% to 86% at meeting length. A
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
