# Module A. Audio Pipeline

| | |
| --- | --- |
| **Package** | `autune_audio` |
| **Owner** | 김민경 |
| **Backend** | `modules/audio/` |
| **Frontend** | `apps/web/src/features/transcript/` |
| **Table prefix** | `aud_` |
| **API prefix** | `/api/audio` |

## Responsibility

Turn a recording into an accurate, speaker-attributed, PII-masked transcript,
then delete the recording. A owns the shared entities `meetings`,
`participants`, and `utterances` — it is the only module that writes them.

A is the critical path. B, C, and D cannot integrate until `TranscriptReady` is
real, so A ships first (roadmap W2).

## Non-goals

- Interpreting what was said — classification is B, topics are C, cross-meeting
  linking is D.
- Storing audio for any purpose. See "Privacy notes".
- Chrome extension capture. That was dropped from the plan; the desktop app
  (Electron, system audio) is Phase 2.

## Inputs

| Source | Form | Notes |
| --- | --- | --- |
| Web upload | Audio file | MVP input path |
| Desktop app | System audio stream | Phase 2 |

## Outputs

| Destination | Contract | Event |
| --- | --- | --- |
| B, C, D | `TranscriptReady` | `autune.transcript.ready` |
| `packages/core` tables | `meetings`, `participants`, `utterances` | direct write |
| Slack | Analysis-complete notification | — |

## Pipeline

1. **Ingest** — receive the upload, write it to `AUTUNE_AUDIO_TEMP_DIR`, create
   the `meetings` row, return `202` with a job ID.
2. **VAD** — silero-vad removes silence and segments speech.
3. **STT** — Whisper transcribes with timestamps. whisper.cpp on CPU when no GPU
   is available.
4. **Diarization** — Pyannote separates speakers into `Speaker 1`, `Speaker 2`, …
5. **Speaker identification** — speaker embeddings matched against enrolled
   voices in `aud_speaker_embeddings`; unmatched speakers keep the label and a
   null `speaker_id`.
6. **PII masking** — regex plus NER over the transcript. Runs **before** the
   first database write.
7. **Delete raw audio** — in a `finally` block, so it happens on failure too.
8. **Persist** — write `participants` and `utterances` with masked text.
9. **Publish** — emit `TranscriptReady` with
   `privacy.original_audio_deleted = true`.

Interim summaries are generated at intervals during long recordings and streamed
to the live transcript view.

## Tables

| Table | Purpose |
| --- | --- |
| `aud_jobs` | Processing job state and progress |
| `aud_speaker_embeddings` | Enrolled voice embeddings per user |
| `aud_masking_events` | Counts of masked spans by category, for the recall metric. **Never the masked content** |
| `aud_corrections` | User corrections to speaker attribution and text, for accuracy improvement |

Plus the shared entities in `packages/core`, which A writes.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/recordings` | Upload a recording, start processing |
| GET | `/jobs/{job_id}` | Job status and progress |
| GET | `/transcripts/{meeting_id}` | Full transcript, masked, for a member of the meeting's team |
| WS | `/live/{meeting_id}` | Live transcription: one masked row per utterance, no speaker, nothing stored — `audio-live-transcription.md` |
| PATCH | `/utterances/{id}` | Correct speaker or text |
| POST | `/speakers/enroll` | Enroll a voice for identification |

### Live transcription runs in the API process

One `Transcriber` lock per process (`live/transcriber.py`). Two meetings live
at once share it and each sees roughly double the delay. An MVP limit: the
condition for moving transcription to a worker is concurrent meetings
actually happening and #258 resolved, and the move is that one class.

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |
| `autune.audio.process_recording` | Upload | `gpu` |
| `autune.audio.generate_interim_summary` | Interval during processing | `cpu_heavy` |

## Slack surface

- `/autune` — start a recording session
- Analysis-complete notification with a link to the transcript
- Confirmation DM when a speaker's identity is uncertain

## AI stack

| Component | Model | Notes |
| --- | --- | --- |
| STT | Whisper (`large-v3`), whisper.cpp on CPU | `AUTUNE_AUDIO_WHISPER_MODEL` |
| VAD | silero-vad | |
| Diarization | Pyannote 4.x, `speaker-diarization-3.1` | `AUTUNE_AUDIO_HF_TOKEN`, licence accepted on **three** gated repos — see `../engineering/environments.md` |
| Speaker ID | The pipeline's own `speaker_embeddings` (256-d) + cosine similarity | Threshold in `config.py`. pyannote 4.x returns a vector per speaker, so no separate embedding model is needed |
| PII detection | Regex + NER | Double detection, recall-weighted |
| Interim summary | LLM | The only LLM use in A |

## Metrics

| Metric | 6 weeks | 3 months |
| --- | --- | --- |
| Diarization DER | ≤ 15% | ≤ 10% |
| PII masking recall | 0.95+ | 0.99+ |
| Processing time | ≤ 1.5× recording length | ≤ 1× |

```bash
# What a corpus can measure, before committing to it
uv run python modules/audio/scripts/inspect_corpus.py <corpus-root>
```

Scoring takes structures rather than a model, so it runs without a GPU and the
number means the same thing across model versions — the same shape as
`autune_extraction.eval`.

| Metric | Needs from the labels |
| --- | --- |
| WER | Reference text |
| DER | Speaker **and** turn boundaries. Text alone cannot produce it |
| PII masking recall | A masked and an unmasked form of the same utterance |

DER comes from `pyannote.metrics`, not a local implementation: optimal speaker
mapping is where a hand-rolled version goes wrong, and a figure that cannot be
compared to published ones is not worth having. Scoring uses a 0.25s collar,
which is the convention those published figures use.

The evaluation corpus is `002. 주요 영역별 회의 음성인식 데이터` from AI Hub —
the same one module B uses, so it is downloaded once. It is committee and
broadcast discussion with a chair, while Autune is for team meetings without
one, so a DER measured here reads optimistically: those meetings have less
overlapping speech than ours will.

`original_form` is unmasked personal data. Read it in memory to produce a
masking hypothesis and never write it, log it, or commit anything derived from
it.

The second corpus is HiKE (`thetaone-ai/HiKE`, Apache-2.0): 1,121 Korean-English
code-switched utterances with a published table to read against. It measures
one thing the in-house recording cannot — how the model survives a language
switch — and nothing the recording can: no glossary, one speaker per row, no
personal data. Its metrics, MER and PIER, are reimplemented in
`autune_audio.eval.codeswitch` and pinned against HiKE's own scoring by a
fixture; the run is `modules/audio/scripts/evaluate_hike.py`. Loanwords in
either script are correct there, and since 2026-09-16 in `term_accuracy` too.
Results: `audio-evaluations/03-hike-large-v3.md`.

## Privacy notes

A carries most of the system's privacy burden. Read
`../architecture/privacy.md` in full before touching this module.

- The recording is deleted in a `finally` block. Success, exception, and
  cancellation all delete it.
- Masking precedes the first write. The unmasked string never leaves the
  function that produced it — not to a log, not to an exception message, not to
  a cache.
- `aud_masking_events` records categories and counts, never content.
- Korean STT accuracy is improved with domain hints and accumulated corrections,
  not by keeping audio.

## Both input paths are in the MVP

Live browser recording and file upload are both MVP, decided 2026-09-08
(issue #18). The design assumed this all along — S06 offers an audio-source
radio, S10 checks the microphone, S12 shows the upload pipeline and S13 shows
the live transcript.

What this costs, and what it does not:

- **Contracts are unaffected.** `TranscriptReady` stays terminal: it is
  published once, when the meeting ends. B, C and D analyse a finished meeting —
  gap detection needs the whole discussion, context linking needs the final
  decisions — so nothing downstream wants partial results.
- **Only the live transcript screen needs incremental data**, and that flows
  from A straight to the frontend over a live channel, never through a contract
  or an event. Keep it that way: an incremental contract would force B, C and D
  to handle partial input for no benefit.
- **A pays for two entry paths** converging on one persistence-and-publish step.
  Both must delete the raw audio and mask before the first write; neither path
  gets an exemption.
- `source` distinguishes them (`web_mic`, `file_upload`) for analytics. It is
  not a branch point for consumers.

## Speaker enrollment

Enrolling a voice is **opt-in, and there are two ways in**. Neither blocks a
meeting: a participant who has enrolled nothing is transcribed with a
`speaker_id` of `null` and a `Speaker N` label, which the contract requires
consumers to handle (`packages/contracts`, `transcript.py`).

1. **Onboarding, offered not required.** Design screen S03 lists "내 목소리 등록,
   약 20초" as one of three checklist items, next to an upload dropzone that
   works whether or not the item is done. S04 is the 20-second modal.
2. **Retroactively, from the confirmation DM.** S16 asks the participant whether
   a run of utterances was theirs; answering yes enrols the voice — "이 음성
   특성이 등록되어 다음 회의부터 자동으로 인식됩니다".

The second path is why an upfront gate is not needed, and it is the better
consent moment. A voice embedding identifies a person more durably than an
utterance does, and §5 of `../architecture/privacy.md` asks for consent that the
participant can act on. S16 asks at the point where the benefit is concrete and
checkable — *these six utterances, are they yours?* — instead of asking for
biometric data before the user has seen what it buys them.

S06 already shows unenrolled participants as "음성 미등록 · 회의 후 화자 확인이
필요할 수 있습니다", so an unidentified speaker is a supported state rather than
a degraded one. See issue #19.
