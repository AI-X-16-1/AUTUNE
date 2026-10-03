# Module A — build history

What was built, what it was measured at, and what each number changed. Written
for the next person who has to decide where this module's effort goes.

Evaluation reports live in `docs/modules/audio-evaluations/` and hold the full
tables. This file is the thread through them: the decisions, the reversals, and
what is still open.

Last updated: 2026-09-28.

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
| Speaker identification | `identification.py`, `tasks.py`, `service.py`, `router.py` | open, branch `audio/speaker-identification` (#6) |
| PII masking — patterns | `masking.py` + `autune_integrations.privacy` | merged (#138) |
| PII masking — spoken numbers | `recognition.py` | open, PR #158 |
| Persist + publish | `persistence.py`, `tasks.py` | merged (#184) |
| Event publishing | `autune_core.events` | merged (#145) |

Speaker **identification** (matching a voice to a person, #6) is built —
`docs/modules/audio-speaker-identification.md`. A confirmed speaker fills
`participants.user_id`; an unconfirmed one still keeps `NULL`. The downstream
bugs section 6 describes as waiting on that column are live from here, not
latent — see that section.

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

**2026-09-22.** Review on PR #328 found seven things worth fixing before this
merges, none of them numbers. The centroid was a repeatedly renormalised
running mean, which drifts toward whichever vectors joined a cluster first;
`Cluster` now keeps the raw summed vector and reads the mean off it fresh
each time, so it is exact regardless of join order. Nothing guarded against a
NaN or zero-norm vector reaching a centroid and poisoning every later
similarity score; one `unit()` function, shared by the tracker and the
embedder, now refuses one. The live and stored paths each re-derived the
speaker head count from the same three settings independently, which is two
places to get the precedence wrong; `AudioSettings.speaker_bounds()` is now
the one place, and the `diarization_*_speakers` fields are validated
`ge=1` at settings load instead of failing confusingly later. A failed
embedding used to cost the rest of the session's labels after one bad
vector; it now costs one row, and only three failures in a row switch
labelling off. And the embedder shared the transcriber's lock, so a slow
embedding could hold up another meeting's decode; it has its own lock now,
and a load failure is remembered so a hopeless model is not retried on every
connection. `LiveSession._row` also needed reordering: masking now runs
before either drop check (empty after masking, then low confidence), and
both drop checks run before the embed-and-label step, so a masked-empty or
hallucinated utterance never reaches the tracker and cannot open or move a
cluster. And `evaluate_live_speakers.py`'s `simulate`/`sweep` used to read
the tracker's own default `min_seconds` instead of the deployed setting, so a
sweep could score a threshold against a different short-utterance rule than
production uses; they now take `min_seconds` as a required keyword, and the
script defaults it to `get_settings().live_speaker_min_s` and prints the
value it used.

### Processing time — the target is met with diarization on the GPU (#394), and the first attempt to measure it was wrong

Measured on a six-person 5m27s recording (327.4 s), stored path, accuracy as
normalised CER against the script that was read. The metric this module signed
up for is **≤1.5× recording length** (`modules/audio/CLAUDE.md`), so 491 s.

**Every row below was checked for a stalled clock.** Celery reports a task's
duration from a monotonic clock while the log lines carry wall time; on a laptop
that sleeps mid-task the two disagree, and the wall figure is then partly sleep.
A first attempt at this table used a run where they differed by 273 s — 989 s
wall against 716 s reported — as the baseline every other number was divided
by. That inflated the shipped configuration's cost by about double and produced
the conclusion that the target was missed by double. **The run was discarded and
the measurement redone.** A timing table without that check is not a
measurement, and this one carries the column.

| Configuration | Whisper | Total | ×audio | Accuracy | Deletions | Record changed | Clock |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `large-v3`, beam 5, **diarization on MPS** | 411 s | **434 s** | **1.32×** | **89.4%** | 27 | — | 433.9 ≈ 433.7 ✓ |
| `large-v3`, beam 1, diarization on CPU | 204 s | 374 s | 1.14× | 82.8% | 151 | 10.7% | 373.9 ≈ 373.7 ✓ |
| `large-v3-turbo`, beam 5, diarization on CPU | 112 s | 289 s | 0.88× | **60.7%** | 358 | 36.2% | 289.5 ≈ 289.6 ✓ |
| `large-v3`, beam 5, `cpu_threads=10` | **3,449 s** | — | — | 89.4%, byte-identical | 27 | 0% | not verified |

"Record changed" is normalised CER against the first row's own transcript.

Where the 434 s goes: Whisper **411 s (95%)**, diarization 16 s, the model load
6 s, decode 0.15 s, and everything after the audio — embeddings, masking,
persist, publish — 0.6 s. **Transcription is the pipeline.**

**The shipped configuration before this work was about 1.78× and over target**,
reconstructed rather than measured: 411 s of Whisper plus the 170 s diarization
took on CPU. Putting diarization on the GPU is what brings it to 1.32×, and it
costs no accuracy at all — see the subsection below. Nothing had to be traded.

**Read the 1.32× with its condition attached.** It is the first row, and that
row is diarization on MPS, which `AUTUNE_AUDIO_DIARIZATION_DEVICE` (#394) turns
on and which is not the default. MPS is Apple silicon only and unverified under
a prefork worker (#329); CUDA is unmeasured. So the claim is "the target is
reachable, and here is the one configuration that reaches it" — not "module A
meets its KPI". On what ships today it is missed, and beam width is not what
closes the gap (@lsh2217 on #389).

**`large-v3-turbo` is rejected.** 3.7× faster on the Whisper stage and 29
points worse, and the shape settles it: deletions rise thirteenfold. It is not
mishearing the meeting, it is dropping about a fifth of it — 500 characters
that never reach B's action items, C's gaps or D's links, because they were
never transcribed. This is the measurement behind `live/backends.py`'s split:
*"the stored path has one engine […] the live path can afford a second one
because a row is display, remade by the stored path after the upload."* On an
11-second live utterance turbo reads the same; on a five-minute file it does
not.

**mlx is rejected for the same reason, without needing its own run.**
`live_mlx_model` is `mlx-community/whisper-large-v3-turbo`, so letting the
stored path use Metal inherits exactly that loss. An mlx `large-v3` would have
to be found and measured; the 2.8× in `backends.py` was measured on turbo and
does not transfer.

**More threads made it several times slower.** `cpu_threads` is a hardcoded `0`
on the stored path — CTranslate2 then picks four on this machine — while the
live path has a setting. Raising it to 10 on a fourteen-core M4 Pro took the
Whisper stage to 3,449 s against a clean 411 s, while returning a
**byte-identical** transcript (61 segments, 651 words, `distinct_ratio=1.0`
both times). That run is the one row here whose clock was not checked, so the
ratio is an upper bound rather than a figure; the direction is not in doubt.
Threads change how long a decode takes and not what it returns, so this was
expected to be the one free lever. It is free and it is negative: ten threads
spread past the performance cores, and every parallel section then waits on an
efficiency core. The hardcoded `0` is right, and a setting whose only
non-default value is harmful was not added.

**Both rejected results reverse the live-channel table above**, and that is the
finding rather than a footnote. That table measured one 11.4 s utterance and
found 10 threads *faster* than 4 (large-v3 6.2 s → 4.6 s, turbo 4.3 s → 2.4 s,
which is the halving `live_cpu_threads`'s docstring cites) and turbo
*"practically the same"* quality as `large-v3`. On a 327 s file the same two
settings are several times slower and 29 points worse.

Neither measurement is wrong. They describe different workloads, and the table
itself says why: *"the encoder pads every segment to a 30 s window, so there is
a ~2 s floor regardless of utterance length."* One short utterance is one
window; a meeting is eleven of them in sequence, where a per-window cost
compounds and a per-window omission accumulates into a fifth of the transcript.

What does not follow is generalising either result. `AUTUNE_AUDIO_LIVE_CPU_THREADS=10`
is set in the demo today on the strength of a single-utterance measurement, and
the crossover was never looked for. Filed as #376. The lesson for this module is
narrower than the numbers: **a decode measurement taken on one utterance says
nothing about a meeting**, in either direction, and the live/stored split is the
thing that has been protecting the record from that mistake.

**`beam_size`'s docstring was wrong, and the correction is not the one it first
looked like.** It said 5 is "what the processing-time target assumes". At the
time of writing that appeared false — the target seemed missed by double — and
beam 1 looked like the only way to reach it, at 6.6 points of accuracy. With the
baseline measured properly and diarization on the GPU, **beam 5 reaches the
target at full accuracy and there is nothing to trade.** Beam 1 remains
available; it is no longer a decision anybody has to make.

The gap between them is **about 207 s, not 60 s.** 60 s is the distance between
two rows of the table that differ in two things at once — beam 5 with
diarization on MPS against beam 1 with diarization on CPU. Held at one
configuration it is the Whisper stage alone: 411 s against 204 s, which would
put a beam 1 run with diarization on the GPU near 227 s, about 0.69×. The
conclusion does not move — beam 1 costs 6.6 accuracy points and 151 deletions
against 27, and nothing needs that speed — but the number was comparing
configurations rather than beams (@lsh2217 on #389).

#### Diarization was 171 s because nobody moved it off the CPU

Measured 2026-09-28 on the same six-person 5m27s recording (327.4 s),
`num_speakers=6`, one process, one waveform, so this is **one recording** and
not a benchmark:

| Device | Time | ×audio |
| --- | --- | --- |
| `cpu` | 163.4 s | 0.50× |
| `mps` | **11.5 s** | **0.035×** |

163.4 s here against the 169–176 s in the runs above is the same stage's
run-to-run spread on the same machine; the row to read is the other one.

`PyannoteDiarizer._load` called `Pipeline.from_pretrained` and never `.to()`.
pyannote builds its pipeline on CPU and stays there, so the 171 s in the table
above is a CPU number on a machine with a GPU — and on a CUDA box the same bug
sent Whisper to the GPU through `AUTUNE_AUDIO_DEVICE` and left diarization
beside it on the processor. One line, 14.3×.

**The 0 ms boundary agreement is what makes it safe to take.** 77 turns and 6
speakers both times, the same label on all 77, maximum drift 0 ms on start
boundaries and 0 ms on end boundaries, total speech 297.4 s against 297.4 s.
Equal counts would not have been enough: `speakers` assigns each word to the
turn containing it, so a device that moved a boundary by 40 ms would move words
between speakers downstream. Nothing moved.

The device is its own setting, `AUTUNE_AUDIO_DIARIZATION_DEVICE`, and not a
third value for `AUTUNE_AUDIO_DEVICE` — that one is handed to faster-whisper,
whose CTranslate2 backend has no Metal support, so `mps` there would break
transcription. Empty follows `AUTUNE_AUDIO_DEVICE`, so a CUDA deployment gets
both stages on the GPU from the variable it already sets.

An unavailable device raises `ConfigurationError` instead of falling back to
CPU with a warning. CPU works, which is exactly the problem: a fallback turns
14.3× into a log line, and section 4 below is a list of failures that looked
like successes until somebody measured.

**That rule cost a working deployment, and review caught it before it shipped.**
Four reviewers converged on the same hole (#394). `uv sync` installs a CPU torch
wheel on Windows while faster-whisper reaches the GPU through CTranslate2's own
CUDA, so `AUTUNE_AUDIO_DEVICE=cuda` is a configuration that works today with
`torch.cuda.is_available()` False — measured by @kjfcvx12 on an RTX 3060. An
empty `DIARIZATION_DEVICE` inherits that `cuda`, so the rule as written would
have failed every meeting on a box that changed no setting of its own, and the
troubleshooting table in `environments.md` recommends exactly that setting.

The distinction the rule was missing is between a device you asked for and one
you inherited. An explicit `DIARIZATION_DEVICE` is a promise and still raises.
An empty one takes CPU and logs `diarization_device_unavailable` naming the
variable — which regresses nothing, because diarization has run on CPU since it
shipped. What it removes is the silence, not the speed.

**And it raised after thirteen minutes of Whisper.** `resolve_device` was
reached only from `_load`, the last step inside the `adopt` block, so the error
arrived after transcription — a mistake knowable before the file was opened,
charged the whole recording. `process` resolves it as the **first line inside**
`adopt` now, before `decode`.

Inside, not in front of it, and that distinction took a second round of review.
The first fix put the call ahead of `adopt`, which left the upload on disk when
it failed; the reasoning was "the sweep will collect it". It would not have:
`sweep_orphans` is for a task that was *lost*, and this one failed. A `failed`
job is never re-run, recovery is a re-upload with a new job and a new file, so
the one left behind had no reader and no owner — the durable copy invariant 11
exists to prevent (@PARKJAEKYUNG0525). Inside the block both things hold: the
failure costs milliseconds, and `adopt`'s `finally` still deletes. Two integration tests hold the
order: one asserts `resolve_device → decode → transcribe`, the other that an
unusable device decodes nothing and leaves the file.

**CUDA is still unmeasured.** The 0 ms agreement is CPU against MPS. Whether a
CUDA box produces the same turns, and how pyannote shares VRAM with Whisper
`large-v3` in fp16, are open; `=cpu` is the way out.

**This is what brings the module inside its processing-time target**, which was
not obvious when it was written: at the time the baseline was thought to be 3.0×
and this looked like an improvement from 3.0× to 2.5×. The baseline was wrong —
it divided by a run that had been asleep — and with it corrected the shipped
configuration was about 1.78×, so moving diarization to the GPU is the
difference between over and under. The whole meeting is **434 s for 327 s of
audio, 1.32×**, and transcription is now 95% of it. Nothing was traded for it:
the transcript is identical to the millisecond.

**What it does not settle.** `mps` has only been measured in-process. Module E's
SetFit aborts on Metal under prefork and threaded Celery workers, which is why
the demo runs `--pool=solo` (#329), and pyannote on Metal inherits that risk
untested — which is why the setting is empty by default and only the demo opts
in.

#### Measured again on the shipped configuration, and the first meeting costs more

The 434 s above was measured on #394's own branch, before #370 put speaker
identification in the same task. Re-run 2026-09-29 on `main` (`438c498`) with
`AUTUNE_AUDIO_DIARIZATION_DEVICE=mps`, a `--pool=solo` worker started fresh,
and the same 327.4 s recording — twice in a row, in one worker:

| | Total | ×audio | Whisper | pyannote load | Diarization |
| --- | --- | --- | --- | --- | --- |
| First meeting after the worker starts | 482.6 s | **1.47×** | 413 s | 50 s | 11 s |
| Every meeting after that | 442.9 s | **1.35×** | 431 s | — | 11 s |

Both are inside the 1.5× the module signed up for, and the 11 s diarization is
the 11.5 s #394 measured, so nothing regressed when identification landed on
top. Transcripts matched the CPU runs from the same day: 45 utterances, 6
speakers, no tracebacks.

**The CPU figure is now measured rather than reconstructed.** Three runs on the
same `main` and the same recording with the setting left empty took 630.9 s,
637.9 s and 654 s — **1.93×**, not the 1.78× this file arrived at by adding a
separately-timed diarization stage to a separately-timed Whisper. Reconstruction
under-counted by about 0.15×, which is the sort of error that only shows up when
somebody runs the whole thing.

**The first meeting is the one to quote.** 434 s was a warm process; loading
pyannote onto Metal takes about 50 s and happens once per worker, so a demo
that starts a worker and uploads one meeting is at 1.47×, not 1.32×. That is
inside the target with about 20 s to spare, which is less margin than a single
number suggests. Uploading anything before the demo pays that cost early and
the meeting that matters runs at 1.35×.

Whisper moved 413 s → 431 s between the two runs on the same machine and the
same file. That spread is larger than the whole diarization stage now, which is
the other thing the single number hides: **transcription is 93–97% of the run,
and everything else is noise around it.** The next real saving is a different
transcription engine or a smaller model, not another stage.

### Speaker identification (`docs/modules/audio-speaker-identification.md`)

`speaker_id` was null on every utterance the module had ever produced: voices
were separated and never named. This adds the missing half — a confirmed
speaker becomes a voice profile, and the next meeting offers that person as a
candidate for the same voice.

The threshold is **0.70, provisional** — higher than the live tracker's 0.55
because that one asks whether a voice is the same as a moment ago and this one
asks whether it is a particular person. The evaluation that settles it needs
several meetings with the same people, which the in-house recording does not
have; it is the next thing this feature owes.

**First measurement, 2026-09-26 — the threshold has a floor now, and still no
ceiling.** A six-person 5m27s recording (`오튠회의샘플_6인.m4a`, local only:
`*.m4a` is git-ignored, and invariant 11 keeps a recording out of durable
storage) diarized into 6 speakers over 77 turns and produced a vector for all
six — nobody fell under the 3-second floor. Comparing the six against each
other gives 15 pairs of **different people recorded in the same room, on the
same microphone**:

| | Cosine similarity |
| --- | --- |
| Closest pair (화자 2 / 화자 6) | **0.382** |
| Mean of 15 pairs | 0.207 |
| Furthest pair | 0.100 |

So 0.70 sits **0.32 above the closest false match** on this recording. Same
mic, same room, same session is the hardest case for telling people apart —
channel and noise are identical, so only the voices differ — which makes 0.382
a meaningful upper bound on the false-match region rather than a lucky number.

A second, weaker check the same day: a profile confirmed from an unrelated
60-second two-person clip was offered to **none** of the six. Correct, and
what the threshold is for.

**What is still unmeasured is the half that matters more.** These numbers bound
the threshold from *below* — they say 0.70 will not confuse two people. They say
nothing about whether it is too *high*, which is the question of how low the
**same** person scores across two different recordings, and that still needs two
meetings with the same people. A threshold that never confuses anyone and also
never recognises anyone is the failure these numbers cannot see.

The vector itself is taken from **3 to 10 seconds** of a speaker's own turns
(`speaker_embedding_min_s` / `speaker_embedding_max_s`) — long enough to embed,
short enough that one straggler turn cannot pull the average toward noise.
Embedding model: `pyannote/wespeaker-voxceleb-resnet34-LM`, 256 dimensions —
the same model `audio-live-speakers.md` already uses for the live path, so a
live vector and a stored vector are comparable without a second download.

**`voice_profiles_enabled` is `False` by default** — the one setting here that
is not a tuning knob but a legal gate. ADR 0007's Q4 (#92) asks whether a
voice embedding is biometric information (sensitive information) under PIPA
Article 23 and whether collecting it needs
its own separate consent, and that question was still open when this feature
shipped.

**It gated only the profile write, and that was the wrong line.** Four
reviewers read the flag as "no biometric data is collected" because the PR said
so; the code stored an observation vector per speaker on every consented
meeting regardless of it (@PARKJAEKYUNG0525, @lsh2217 on #370). Those vectors
are the same data Q4 asks about. They become attributable to a person the
moment a speaker is confirmed. The consent behind them is one checkbox reading
"녹음과 분석", which does not mention voice characteristics. And they outlived
the flag: turning it on later and confirming a speaker copied a vector recorded
before anyone could have consented to enrolment straight into that person's
profile.

So the gate moved to collection. With the flag off the embedder is never
loaded, no vector is taken, and a meeting reprocessed after it goes off gives
back the vectors it had — the DELETE now runs whether or not anything replaces
it, which also fixes a re-run leaving a first pass's vectors under labels a
second diarization had reassigned. `Participant.user_id` is still written (that
is attendance), and deleting a profile is never gated — a flag that limits
collection must not block its own undo.

The lesson is narrower than the fix: **a privacy claim in a PR description is
not a test.** "Merging this collects no biometric data" was written in good
faith about a gate that existed, one layer away from the collection it was
describing. What settles it now is `test_the_flag_being_off_collects_no_vectors_even_with_consent`,
which asserts the embedder was never even loaded.

Turning it on is expected to wait for authentication to exist and carry a
separate, refusable biometric consent (#268); until then the cost of having
shipped identification ahead of the legal answer is a flag flip, not a rebuild.

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
owner; a live job is left alone until `orphan_after_hours`.

**Then it got a second trigger, and kept the first** (#207). The in-task call
fails exactly when it is needed: the situations that leave an orphan behind are
the situations where uploads stop, so "the next upload will collect it" is not a
guarantee. `autune.audio.periodic.sweep_orphans` runs it hourly on beat instead,
owning no job and therefore sparing no file. Both are kept because they fail
differently — the in-task one is the only one that fires with no beat process
running, which is every local run and every demo, and the periodic one is the
only one that fires when nothing is being uploaded at all. Hourly, not sooner:
a `queued` or `running` job holds its file until `orphan_after_hours` (6h), so a
shorter interval scans the whole upload directory and collects nothing extra.
The mechanism that made this possible is `autune_core.periodic`, added in the
same PR — a module declares a periodic task by defining it, and `apps/worker`
gains no line (invariant 6).

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
- `live/session.py` said `# The same recogniser the stored path uses, chosen by
  the same setting.` — the stored path passed none, so a number read out as
  words was masked live and written in the clear by the batch path (found in
  review of #484, where those rows go to an external verifier).

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

That last clause is the one that broke. Both sides used `find_pii` and neither
passed `get_recogniser()`, so both agreed — and both were half the masker. The
live path had passed a recogniser since it was written, so `010` said aloud was
masked in the live channel and stored in the clear by the batch path. The fix is
one argument at each of the two call sites, and the reason it is two and not one
is the same as the row above: the guard checks what the masker promises, so it
has to see everything the masker sees.

**Separators, derived instead of copied, 2026-10-02 (#324).** The masker kept
its own list of the characters `privacy._SEP` accepts between digit groups,
and the two drifted a third time: a thin space the pattern took made a phone
number "mixed script" and all of it went, and a card read across lines lost
its last four. Over-masking, not a leak, but the documented shape broke. The
masker now treats any whitespace and the punctuation `privacy` exports as
layout, so the list exists once. The first version also stopped `_HSPACE`
counting `\v`, `\f`, U+0085, U+2028 and U+2029 as space, to keep a phone
match from bridging two lines; review showed it left a nine-digit `02`
number split by one of them matched by nothing, through the outbound guard,
so it was reverted -- detection stays wide, and only the masker's layout
changed. A newline-split `02` number is missed on `main` too (#688). The masking corpus scored the same before
and after (recall 1.000, precision 1.000, 32/35 exact, the 3 declared rows
unchanged) -- it has no row with any of these characters, which is itself
the gap: the regressions are pinned in unit tests, not in the corpus.

**Phones across lines, 2026-10-02 (#688).** Reverting the `_HSPACE` change in
#687 left a gap that was there on `main` all along: a nine-digit `02` number
split by newlines matched nothing, because `account` needs ten digits and the
phone pattern does not cross a line. A second phone pattern on the card's
narrow separator closes it. The leading zero and the 2-3 / 3-4 / 4 layout
are what keep it from joining figures on adjacent lines; the corpus scores
the same, and `예산\n150000\n200000` is matched exactly as before (by
`account`, a known over-mask). What it newly over-masks, found in review and
accepted as the safe direction: a short zero-led figure at a line end
followed by a three-to-four and a four-digit line -- `Q1 05\n300\n2500원`,
`목표 01\n300\n2024`. If a false positive is reported, look at this pattern
first.

**Numbers read aloud, 2026-10-02 (#160, evaluation 04).** #160 asked for a
count of comma-split and one-syllable-at-a-time numbers before widening a
pattern. There was nothing to count -- 218 stored utterances and HiKE's 1,121
references hold one number between them -- so 96 synthetic clips were made
(TTS, one voice, two rates). Whisper wrote **every** number in Arabic digits:
#160's shapes and the recogniser's `공일공` never appeared. What leaked instead
was grouping: Whisper hyphenates by guess, and an account or resident number
grouped wrongly matches no length-keyed pattern -- 15/32 accounts and 13/32
resident numbers leaked, 0/32 phones. The decision this drives: don't fix
#160's shapes; measure a total-length catch-all for hyphen-joined digit runs
against the corpus (the `2024-2025-2026` false positive is its known cost).
TTS is not a meeting, so the rates are not field rates.

**Grouped digits, 2026-10-02 (#696).** Evaluation 04 (PR #697) found that
Whisper writes a number read aloud in digits and guesses its hyphens, and the
length-keyed patterns let mis-grouped accounts and resident numbers through
(28 of 96 synthetic clips). The fix is one rule in `privacy.py`: a run of
digit groups joined by a hyphen, a dash or the filler Whisper keeps (음, 어)
is personal data once it holds eleven digits, however it is split -- refused
when it is a list of years or a date range, and refused whole so a date range
is not retried from the inside. Not a bare space or a dot: price lists and
version strings. Re-run of the same 96 clips: **1 leak**, an account Whisper
wrote run-together (`45080930978`), which keeps its last four by the old
`account` rule and counts only because the hyphenated baseline now keeps none.

The trade, decided rather than discovered: the rule is declared above
`account`, so a correctly hyphenated account loses its last four. A resident
number Whisper hyphenated 6-3-4 is also an account shape, and the alternative
was four of its digits standing. Two corpus rows (3-3-6, 6-2-6 accounts) now
differ in the safe direction and say so (`known_inexact`); recall and
precision stay 1.000. TTS, one voice: not field rates.

Review moved one line. The date exemption first refused any run whose groups
were all four digits or fewer and started with 19xx/20xx -- which is exactly
the shape of a number Whisper split into short groups, so `2008-26-643-8793`
passed as a date. It now refuses only a year followed by one- or two-digit
groups or more years. What the rule newly over-masks, accepted: hyphenated
lists past eleven digits (`100-200-300-400-500명`, an ISBN). The filler `어`
is a separator on the strength of `음` alone; the next corpus run should
say whether any match is joined only by `어`.

**Retention and deletion, 2026-10-01 (#581–#584, then #363).** Four rows that
were document-only became code: an hourly sweep deletes meetings past
`expires_at` (`retention.py`); a person can export, delete their speech, or
delete their account (`account.py`); a reported PII miss is masked where it is
stored and `TranscriptReady` republished (`pii_report.py`). Two decisions are
the kind this file exists to keep:

- **The retention window starts when the meeting is held**, not when it is
  booked. The first version anchored it on creation, and review found a
  30-day team's meeting booked five weeks ahead would be swept within the hour
  of starting, mid-recording. Reproduced by a test that fails on the old
  commit.
- **A voice profile is bounded by meetings, two ways.** A profile row now
  cascades with the meeting it was confirmed in (#363 item 2; it was
  `SET NULL`, which left a wrong confirmation unreplaceable forever), and the
  sweep deletes whatever is left once no meeting names the person. A profile
  is therefore the mean of the person's confirmations inside the retention
  window. What this costs is not measured: identification for someone who
  attends rarely now starts over after each window, and no evaluation says
  how many confirmations a usable profile needs. That number is owed by the
  threshold evaluation in section 2, not by this change.
- **The embedding width is checked at load** (#363 item 3). `EMBEDDING_DIM`
  and the checkpoint were kept in sync by hand, and a mismatch would have
  failed every vector INSERT inside the guard that keeps a meeting's
  transcript safe from a bad vector -- silently. `Embedder.warm_up` now reads
  the model's `dimension` and refuses; the stored path logs it at error level.

---

## 6. What is open, and why it matters

### Speaker identification shipped (#6) — the two bugs it wakes up are live now

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

Both were latent while `user_id` was always `NULL`. **They are live now**, on
`audio/speaker-identification`: the day a real meeting gets a confirmed
speaker, both bugs are reachable, not hypothetical. #167 wrote the rule down
once, in `docs/architecture/data-model.md` under "A participant row is a
voice, not a person" — **merged** (2026-09-15), so the rule was already a
statement, not just two local fixes, before this feature shipped.

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
| #92 | Legal review of ADR 0007 — Q4 gates whether embedding collection needs separate consent. #6 shipped without waiting for the answer, gated instead on the meeting's existing consent attestation; whether that is enough is still #92's open question |
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
   expected return: GPU; `large-v3-turbo` (unmeasured); batching. The targets
   are two, both end to end (`docs/modules/audio.md`): ≤ 1.5× recording
   length at six weeks, ≤ 1× at three months. Measured, transcription is 0.73
   and diarization 0.54, **1.27× for the two model stages** — inside the
   six-week budget with 15% of it left, outside the three-month one by 27%,
   and not yet end to end: decode, masking and the write are unmeasured, and
   a short file does not pass (2m45s is RTF 1.19 for transcription alone).
   Eval-02 once read the two targets as one and called this a miss; corrected
   in #186. Still second on this list: the six-week number passes only on an
   11-minute file with a thin margin, and the three-month one does not pass
   at all without the GPU.

3. **An evaluation set.** Every threshold in this module is a placeholder chosen
   from one recording: the masking thresholds, the repetition guard, the glossary
   mode. Four open detector issues cannot be resolved without one, and the
   `#61` target argument is about what a number means under which conditions.

4. ~~**Speaker identification (#6)**, once #92 answers.~~ Shipped on
   `audio/speaker-identification` without waiting for #92 — see the table
   above and section 6. The threshold evaluation section 2 describes is what
   this feature still owes, not the identification itself.

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
