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
| GET | `/transcripts/{meeting_id}` | Full transcript |
| PATCH | `/utterances/{id}` | Correct speaker or text |
| POST | `/speakers/enroll` | Enroll a voice for identification |

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
| Diarization | Pyannote | Needs `AUTUNE_HF_TOKEN` with licenses accepted |
| Speaker ID | Speaker embedding + cosine similarity | Threshold in `config.py` |
| PII detection | Regex + NER | Double detection, recall-weighted |
| Interim summary | LLM | The only LLM use in A |

## Metrics

| Metric | 6 weeks | 3 months |
| --- | --- | --- |
| Diarization DER | ≤ 15% | ≤ 10% |
| PII masking recall | 0.95+ | 0.99+ |
| Processing time | ≤ 1.5× recording length | ≤ 1× |

```bash
uv run --package autune-audio python -m autune_audio.eval
```

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

## Open questions

- Real-time streaming transcription versus post-upload batch for the MVP.
- Speaker-enrollment UX: prompt on first meeting, or opt-in later.
