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
| `aud_jobs` | One row per transcription attempt: `queued` → `running` → `done` / `failed`, or `superseded` by a later attempt. What the worker is queued instead of a path |
| `aud_speaker_embeddings` | Enrolled voice embeddings per user |
| `aud_masking_events` | Counts of masked spans by category, for the recall metric. **Never the masked content** |
| `aud_corrections` | User corrections to speaker attribution and text, for accuracy improvement |

Plus the shared entities in `packages/core`, which A writes.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/teams` | The teams the caller may open a meeting for; feeds `POST /meetings` |
| POST | `/meetings` | Open a meeting for a team, before there is any audio |
| GET | `/meetings/{meeting_id}` | Title, status and the two privacy flags. What S12 polls |
| POST | `/meetings/{meeting_id}/recording` | Upload a recording and queue transcription (202) |
| GET | `/jobs/{job_id}` | Job status and progress (planned) |
| GET | `/transcripts/{meeting_id}` | Full transcript, masked, for a member of the meeting's team |
| POST | `/meetings/{meeting_id}/consent` | A member attests that everyone in the recording consented (#190) |
| WS | `/live/{meeting_id}` | Live transcription: one masked row per utterance, no speaker, nothing stored — `audio-live-transcription.md` |
| PATCH | `/utterances/{id}` | Correct speaker or text |
| POST | `/speakers/enroll` | Enroll a voice for identification |

### Live transcription runs in the API process

One `Transcriber` lock per process (`live/transcriber.py`). Two meetings live
at once share it, and once their combined load exceeds real time the delay
grows for the rest of the meeting rather than doubling — nothing is dropped,
so every row is late by everything queued before it. An MVP limit: the
condition for moving transcription to a worker is concurrent meetings
actually happening and #258 resolved, and the move is that one class.
`live/registry.py`, the one-session-per-meeting claim, is a dict in that
process. The route takes it once the hello's transaction has committed and
releases it before `ended`; `service.start_transcription` refuses an upload
(409) while it is held. The API runs with **one uvicorn worker**
(`environments.md`): a second worker would let a second session onto the
same meeting, and would accept an upload the first worker's claim should
have refused.

Live rows are masked one at a time, so a number read with a pause in it
reaches the screen unmasked across two rows; the stored transcript is the
masked final form (design §3.5).

Every live row carries a speaker cluster label, `화자 N`: one embedding per
utterance from the diarizer's own embedding model, nearest-centroid
clustering in the session, a number that never changes once shown, and a
cap from the same head-count hint the stored path gives pyannote. Nothing is
stored; a session whose embedder fails shows `?` and goes on. The stored
path uses the same `화자 N` text, numbered by who spoke first. Design and
threshold evaluation: `audio-live-speakers.md`.

### Consent, until there is a per-person consent flow

`participants.consented` had no writer at all (#190): every real meeting came
out of B and C empty, because both analyse only consented utterances. Screen
S10's per-attendee consent table cannot exist before identification (#6) gives
a voice a person, so the one honest statement available is a team member's
about the whole meeting. Any member of the team may make it — not only the
uploader, and not only someone who was in the room; `meetings` has no
`created_by` to narrow it further.

`POST /meetings/{id}/consent` records that statement in
`aud_consent_attestations` (who, when; one row per meeting) and sets every
participant row of the meeting to `True` — the rows that exist now, and, through
`persistence._participants_for`, every row a later run creates, so a rerun that
invents a new speaker label gets the same value. The row is the provenance: when
S11 lands and consent can also arrive per person, a `True` from here and a
`True` from there stay distinguishable.

What it is not: per person, revocable, or a re-publish. Deleting the
attestation row does not un-attest — nothing sets a participant back to
`False` — so there is no route that deletes it. Nor is there a way to withdraw
what B, C and E derived once the meeting was analysed, short of deleting the
meeting; before identification (#6) there is no per-person unit to withdraw
for. A meeting with no attestation is exactly as before — stored, not
analysed. When the per-attendee table exists this route is derived from it or
removed, and revocation is defined there.

Creating the meeting is a separate call from uploading to it, rather than the
single `POST /recordings` this table used to plan. The live-microphone path
(S10/S13) has a meeting well before it has a recording, and `meetings` is a
shared entity only module A may write — one writer, one place, reached the same
way by both paths.

### Meeting status, and who moves it

`meetings.status` had no writer at all before the upload endpoint existed. A
owns these four transitions; the rest belong to B and E, later in the meeting's
life.

| From | To | When |
| --- | --- | --- |
| — | `scheduled` | `POST /meetings` |
| `scheduled`, `recording` | `recording` | a live socket's `hello` (`service.begin_live`) |
| `scheduled`, `failed` | `analyzing` | a recording is accepted and queued |
| `recording` | `analyzing` | the browser's upload after `stop`; refused 409 while this process still holds the meeting's live claim |
| `analyzing` | `complete` | `process_recording` wrote the transcript |
| `analyzing` | `failed` | the task raised, or the enqueue never reached the broker |

`failed` and `recording` are the statuses other than `scheduled` that accept a
recording — `failed` for recovery, `recording` because the live channel's
upload is what moves the meeting on. A `complete` meeting refuses one: its
transcript has already gone out to four modules, and replacing it underneath
them is the rerun problem in #194.

### The recording between the two processes

The endpoint writes the upload to `AUTUNE_AUDIO_TEMP_DIR`; the file therefore
outlives the request. privacy.md section 1 now says how that is allowed
(decision #275): the file is **owned by exactly one party at a time** —
`storage.handover` until the task is queued, `storage.adopt` from then on —
and the two ends **never exchange a path**. The endpoint creates an `aud_jobs`
row, renames the file to `{job_id}.upload`, and queues the job id; the worker
rebuilds the path with `storage.upload_path`. The Celery message, the broker
and Celery's failure output carry an opaque id.

`aud_jobs` is one row per *attempt*, not per meeting. A `failed` meeting
accepts another recording; the earlier attempt is marked `superseded` and the
worker declines it at the door (`service.claim_job`), so a task that turns up
late cannot race the current one or fail its meeting. A redelivery of a job
already `done` is declined the same way; one arriving while the first delivery
is still `running` is declined and leaves the file to its owner.

**Deployment assumption: the API process and the `gpu` worker share a
filesystem.** The task is handed a local path, not bytes. If the two run on
different hosts or in containers without a shared mount, `adopt` receives a
path to nothing and the job fails. Document the mount in the deployment, or
this endpoint does not work.

**There is a second copy during the request, outside `AUTUNE_AUDIO_TEMP_DIR`.**
Starlette's multipart parser spools a file part to the OS temporary directory
(`tempfile.gettempdir()`) before the route function runs, and only the
non-file fields are subject to its size limit. So the whole body is on disk
before `MAX_UPLOAD_BYTES` is checked, and `_reject_persistent` never sees that
path. Starlette deletes it when the request closes, so invariant 11 holds — but
a request-body limit belongs at the reverse proxy or in `apps/api`, not here.
Tracked as a shared issue.

**A file whose task is lost after the enqueue is collected by the sweep.**
`service.sweep_orphans` compares every file in `AUTUNE_AUDIO_TEMP_DIR` against
`aud_jobs`: a file whose job is `done`, `failed` or `superseded` is deleted;
a `queued` or `running` job older than `AUTUNE_AUDIO_ORPHAN_AFTER_HOURS` is
failed and its file deleted. A file no job knows — written but not yet
claimed, or renamed but not yet committed — is deleted only once it is older
than that threshold, since a request may still be inside it. #209 swept on
mtime and could delete a file a late task was about to adopt; deciding against
the database is what makes this one safe. It runs at the start of every
`process_recording` until there is a periodic trigger (#207).

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
