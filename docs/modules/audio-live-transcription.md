# Live transcription — design

**Date:** 2026-09-18 · **Owner:** 김민경 · **Module:** A · **Status:** Built
(`audio/live-transcription`); what differed from this document is in the
last section

Browser microphone in, one transcript row per utterance on screen S13 while the
meeting is happening, the whole recording through the existing upload path
when it stops. This is the second of the two MVP inputs `modules/audio/CLAUDE.md`
names; the first (file upload) exists (#259).

---

## 1. What is decided, and why

| Decision | Choice | Reason |
| --- | --- | --- |
| Who holds the full recording | **The browser.** `MediaRecorder` keeps the blob in memory; on stop it is uploaded through `POST /meetings/{id}/recording` | The server never accumulates raw audio. Invariant 11 stays simple, and the final pipeline (diarization, masking, persistence, `TranscriptReady`) has exactly one entry point |
| Unit of a live row | **One utterance**, cut by silence (VAD). A row, once sent, never changes | S13's `LiveTranscript` is built on rows that do not move. Caption-style revision would need a different screen and cannot keep up on CPU |
| Speaker during recording | **None.** Rows say "unidentified"; the final pipeline attaches speakers after the upload | pyannote needs the whole recording. Online diarization is a new stack and a label-reconciliation problem, not for W3 |
| Where live transcription runs | **In the API process**, over a WebSocket, Whisper loaded lazily on the first connection | The only option that depends on none of the open decisions (#258, #275). One moving part. The seam that would move it to a worker is one class |
| Masking on live rows | **Yes**, the same `mask()` | S13 draws PII tokens. Nothing is stored, but there is no reason to show a number on screen that the stored transcript will not |
| Classification during recording | **None.** `kind` is absent on every live row | #155: the live channel carries no partial results into any contract or event |
| New dependencies | **None** | silero VAD ships inside faster-whisper (`faster_whisper.vad`); `websockets` is already installed by `uvicorn[standard]` |

## 2. Shape and protocol

```
browser (LiveMeetingScreen)                       FastAPI (apps/api, module A router)
──────────────────────────                        ─────────────────────────────────
getUserMedia ─┬─ MediaRecorder → blob (memory)    WS /api/audio/live/{meeting_id}
              ├─ AnalyserNode  → levels (screen)    │
              └─ AudioWorklet  → PCM16 16 kHz ──WS──▶ LiveSession
                                 ~200 ms frames         ├─ ring buffer (memory, bounded)
                                                        ├─ Segmenter (silero VAD endpointing)
                                                        ├─ Transcriber (thread pool, one lock per process)
                                                        └─ mask() → Utterance ──WS──▶ a row on screen
[stop] → ws.send(stop) → ws.close()
       → POST /meetings/{id}/recording (blob) ──▶ the existing worker pipeline
       → router.push(/meetings/{id})
```

Text frames are JSON; binary frames are audio.

| Direction | Message | When |
| --- | --- | --- |
| C→S | `{"type": "hello", "token": "…"}` | First message after connect. The token is here, **not in the query string** — URLs end up in logs |
| S→C | `{"type": "ready"}` | Authenticated and the model is loaded. No audio is accepted before this |
| C→S | binary | PCM16, mono, 16 kHz, about 200 ms per frame |
| C→S | `{"type": "pause"}` · `resume` · `stop` | The rail's buttons |
| S→C | `{"type": "row", "utterance": {…}}` | One utterance, final. A `contracts.Utterance` as-is: `id` is `utt_live_…`, `speaker_id` is `null` (which is what `TranscriptRow` keys "unidentified" on) with `speaker` set to one display label for the whole session, `text` is masked |
| S→C | `{"type": "error", "code": "…"}` | One segment failed, or a frame was refused. The session continues |
| S→C | `{"type": "ended"}` then close | `stop` has been processed and the last segment sent |

Close codes: `4401` no or invalid token · `4403` not a member of the team ·
`4404` no such meeting · `4409` a session is already open for this meeting ·
`4503` the model could not be loaded.

**Why PCM and not `MediaRecorder` chunks.** A webm chunk is not decodable
without the container header from the first chunk, so a server would have to
keep an ffmpeg pipe open per session. An `AudioWorklet` that resamples to
16 kHz and emits Int16 gives the server a `Waveform` in one `np.frombuffer`,
at exactly `schemas.SAMPLE_RATE`. The full recording for upload is a separate
`MediaRecorder` on the same `MediaStream`, and stays webm/opus — ffmpeg decodes
that in the worker as it does any upload.

## 3. Server — `modules/audio/src/autune_audio/live/`

```
live/
├── __init__.py      # invariant 11 for this path, in one paragraph (section 3.5)
├── routes.py        # @router.websocket("/live/{meeting_id}"): hello, the loop, close codes
├── session.py       # LiveSession: state, frames in, segments → rows out
├── segmenter.py     # utterance boundaries on top of silero VAD
└── transcriber.py   # the one file that changes if this moves to a worker
```

Mounted by `autune_audio.router` like the dev router, so `apps/api` picks it up
by iteration and nothing under `apps/` changes.

### 3.1 `Segmenter`

Takes 200 ms PCM frames, runs silero VAD (`faster_whisper.vad`) on each, and
applies three rules:

- speech of at least 300 ms followed by silence of at least 700 ms → a segment;
- a segment reaching 30 s is cut there (Whisper's window);
- frames that contain no speech are dropped, so silence never accumulates.

It returns a `Waveform` (float32, 16 kHz) with `start` and `end` in seconds
from the start of the session, computed from the frame count and never from
the wall clock. As close to a pure function as a stateful cutter can be, and
tested without a model.

### 3.2 `Transcriber`

Wraps `pipeline.transcribe(waveform, glossary=build_prompt())`. Two properties:

- **One lock per process.** CPU RTF is 0.73, so segments that arrive while one
  is being transcribed wait their turn. Two meetings live at once share the
  lock; once their combined load passes real time the delay grows for the
  rest of the meeting rather than doubling (§9). An MVP limit, stated in
  `docs/modules/audio.md`.
- **Runs in a thread** (`anyio.to_thread.run_sync`) so the event loop keeps
  serving frames and other connections.

The model loads lazily on the first connection; `ready` is not sent until it
has. This class is the seam: if live transcription moves to a worker (approach
B in the brainstorm — after #258 lands and concurrent meetings exist), its body
becomes "send to the worker and await the result" and nothing else changes.

### 3.3 `LiveSession`

One connection, one session. Frames go to the `Segmenter`; each segment goes
to the `Transcriber`, through `mask()`, into a `contracts.Utterance`, and out
as a `row`. `pause` drops incoming frames; `stop` flushes whatever the segmenter
holds as a last segment, then `ended`.

### 3.4 Meeting status

`hello` accepts a meeting at `scheduled` or `recording` and sets `recording`.
`stop` leaves it at `recording` — the browser still holds the blob and must be
able to upload it, and so must a browser whose socket dropped without a `stop`.
Therefore `service.start_transcription`'s accepted set gains `recording`
(one line on top of #259). A second `hello` for a meeting with an open session
is `4409`.

### 3.5 Invariant 11 on this path

> Audio exists here as frames in memory and nowhere else. A segment is dropped
> the moment it has been transcribed; the ring buffer is bounded and discards
> its oldest frames first; no code in this package opens a file. The unmasked
> transcription is a local variable in `LiveSession` and reaches no log, no
> exception and no row. When the connection closes, the buffers go with the
> session. This path stores nothing, so it writes no `aud_masking_events`
> either — that is the final pipeline's, after the upload.

Storing nothing is the simplification the whole design rests on. The live
channel is display; the truth is always made by the upload afterwards. That is
also why live row ids are `utt_live_…` and never equal the stored `utt_…`: the
two are not claimed to be the same utterance.

## 4. Browser — `apps/web/src/features/transcript/`

`LiveMeetingScreen` currently plays a fixture on a timer. Two hooks replace
that; `LiveTranscript` itself is not touched — its props already describe what
arrives.

```
LiveMeetingScreen
 ├─ useMicrophone()        getUserMedia → MediaStream; AnalyserNode → levels[]
 ├─ useLiveSession(id)     WS client; rows[], state, elapsed, stop()
 │    ├─ AudioWorklet (public/pcm-worklet.js) → Int16 16 kHz → ws.send(ArrayBuffer)
 │    └─ MediaRecorder → chunks[] → Blob on stop
 └─ LiveTranscript         unchanged
```

### 4.1 `useLiveSession` state machine

```
idle ──start()──▶ connecting ──ready──▶ recording ⇄ paused
                      │                     │
                      └─ error ◀────────────┘ stop()
                                            ▼
                                        uploading ──202──▶ router.push(/meetings/{id})
                                            │
                                            └─ upload_failed  (blob still in memory; a retry button)
```

- `start()`: microphone permission → connect → `hello(token)` → on `ready`,
  start the worklet and the `MediaRecorder` together. The token comes from the
  same source `authHeaders()` reads (localStorage, then the build-time
  variable).
- `stop()`: stop the worklet → `ws.send(stop)` → wait for `ended` (the last
  segment's row arrives before it) → stop the `MediaRecorder` → `Blob` →
  `POST /meetings/{id}/recording` → navigate.
- **A dropped socket does not stop the recording.** `MediaRecorder` does not
  depend on the socket. The screen shows a banner — live transcription lost,
  recording continues — and `stop()` uploads as normal. No reconnection in the
  MVP: losing the live view for a while and losing the recording are different
  orders of failure, and only the second is prevented.
- **Closing the tab loses the recording.** Accepted in section 1. One
  `beforeunload` warning.

### 4.2 The gate before start

S10's per-attendee consent table does not exist. The minimum that keeps
consent honest: a checkbox — "everyone in this meeting has agreed to be
recorded and analysed" — which on tick calls `POST /meetings/{id}/consent`
(#283) and records the consent. It does not block `start()`: recording
works without it and B and C analyse nothing, and the copy under the
checkbox tells the user that this is what skipping it costs. The button
waits only while a consent request is in flight, so a tick is not lost to a
start that races it.

### 4.3 Memory

An hour of webm/opus is 30–60 MB. PCM does not accumulate — frames are sent
and dropped. The 3-hour ceiling (S03) fits in browser memory.

### 4.4 Files

| File | Change |
| --- | --- |
| `components/LiveMeetingScreen.tsx` | Fixture out; the two hooks; the consent gate; the banner |
| `hooks/useMicrophone.ts`, `hooks/useLiveSession.ts` | New |
| `api.ts` | `uploadRecording(meetingId, blob)`, `attestConsent(meetingId)`, a WS URL helper |
| `public/pcm-worklet.js` | New, about forty lines: 48 kHz → 16 kHz, Float32 → Int16 |
| `fixtures/live-demo.ts` | Deleted. #252 said "until the microphone does"; this is when |

## 5. Errors, limits, authentication

### 5.1 Authentication

`hello.token` goes through `autune_core.auth.decode_token`, then
`require_team_member`. Not a `Depends` — a WebSocket handler calls it directly
— so both live in one function, `service.authenticate_live(session, token,
meeting_id)`, and the HTTP routes and the socket cannot come to different
conclusions about the same token.

### 5.2 Limits

All in `AudioSettings`, with these defaults:

| Limit | Default | Past it |
| --- | --- | --- |
| Waiting for `hello` | 5 s | `4401` |
| Session length | 3 h (S03) | `ended`, normal close — the recording is in the browser |
| Frame size | 32 KB (about 1 s of PCM) | frame dropped, `error(frame_too_large)`, session continues |
| Ring buffer | 60 s | oldest frames dropped — when transcription falls behind, audio is what is lost |
| Segment | 30 s | cut (section 3.1) |
| Sessions per meeting | 1 | `4409` |

### 5.3 Three failure domains, handled differently

| Where | Handling |
| --- | --- |
| `transcribe()` raises | one `error(transcribe_failed)`, **session continues.** That stretch is missing from the live view; the recording goes on and the final pipeline sees it |
| The socket drops | session discarded, buffers gone, meeting stays `recording` so the browser can upload |
| The model fails to load on first connect (HF token, licence) | `error(model_unavailable)`, `4503`. The screen degrades to "live transcription unavailable, recording continues" — `MediaRecorder` needs no server |

The third is the one that matters: **if live transcription is down entirely,
nobody loses a recording.** The live channel is display, the recording is the
browser's, the truth is the upload.

### 5.4 Concurrency

One `Transcriber` lock per process (section 3.2). Two meetings live at once
take turns. Documented as an MVP limit with the condition for moving to a
worker: concurrent meetings actually happening, and #258 resolved.

### 5.5 Logs

`live_session_opened` (meeting_id) · `live_segment_transcribed` (meeting_id,
seconds, chars) · `live_session_closed` (meeting_id, rows, reason). No text,
no audio, no token on any line.

## 6. Tests

**Unit, no database**

- `Segmenter` on synthetic PCM (a tone and silence): two utterances with 700 ms
  between them → two segments; a sound under 300 ms → no segment; 31 s of
  continuous speech → cut at 30 s; `start`/`end` agree with the frame count.
- `Transcriber`: the lock serialises — two fake `transcribe` calls submitted
  together do not overlap.
- Protocol: a `row` validates as `Utterance`; an unknown `type` yields
  `error`.

**Integration, `TestClient.websocket_connect`, a fake `Transcriber`**

- no `hello` / bad token → `4401`; non-member → `4403`; no meeting → `4404`;
  second connection → `4409`.
- hello → ready → PCM frames → a `row` whose `text` is masked: a fake
  transcription of `010-1234-5678` arrives as `010-****-5678`.
- `stop` → the last segment's row → `ended` → meeting status is `recording`.
- **Nothing touches disk**: `AUTUNE_AUDIO_TEMP_DIR` is empty before and after.
- **Nothing in the logs**: the fake transcription's words do not appear in
  `caplog`.
- `transcribe` raising → one `error`, the next segment is normal.

**Frontend** — no test infrastructure (#106). `tsc`, `eslint`, `next build`,
and one run with a real microphone; the manual steps go into
`docs/engineering/demo-runbook.md` section 3.

## 7. What this does not do

- Reconnect a dropped socket.
- Show a speaker, or a kind, on a live row.
- Keep the recording if the tab closes.
- Run more than one transcription at a time per process.
- Store anything.

Each is a limit chosen for the MVP and named where a user or a reviewer would
look for it — the screen, `audio.md`, or this file.

## 8. Dependencies on other work

| | |
| --- | --- |
| #259 | `POST /meetings/{id}/recording` and `start_transcription`; this adds `recording` to its accepted set |
| #283 | `POST /meetings/{id}/consent` for the gate |
| #258 · #275 | **Not depended on.** Both concern the upload → worker leg. The live channel does not touch a queue |
| #155 | Consistent with its default: no classification during recording |

## 9. What differed when it was built

§5.2's 60-second ring buffer was not implemented. The segmenter holds at most
one open utterance (≤ 30 s) and drops silent frames as they arrive, which
bounds memory more tightly than a ring buffer would.

The route claims the per-meeting registry immediately after the "already
live" check, before the model warm-up, under one `try`/`finally` — a client
that leaves during a cold model load cannot lock the meeting at 4409.

A real user who is not on the team raises `NotATeamMemberError` (a
`PermissionDeniedError` subclass) from `require_team_member`, so the socket
can close 4403 while every HTTP route still answers 403. A well-signed token
whose user row is gone is refused as 4401 on the socket (the HTTP path says
404), because the socket's 4404 means "no such meeting".

`TranscribeFailed` is raised `from None`: the cause's message can quote what
the model was reading, and the exception chain must not carry it into a
traceback log.

In the browser, a refusal before `ready` (4401/4403/4404/4409) stops the
recorder and lands on an error state rather than "recording without the live
view"; only a transport failure or a model that cannot load (4503) degrade to
recording-only. §4.1's independence of recorder and socket holds for those.

Each browser `start()` takes a generation number; late events from an
abandoned socket or recorder (React StrictMode remounts) are ignored by the
session that replaced it.

The "text is not in the log" tests read stdout, because the project's
structlog logger prints and never reaches `caplog`.

The runbook section for the live path (§3.7 in the plan) is deferred until
`docs/engineering/demo-runbook.md` lands on `main`.

Measured per-row lag at real-time pacing on an Apple-silicon CPU with
`large-v3` was 7–10 s: each utterance of about 10 s takes about 9 s to
transcribe, so a row lands roughly one utterance after the one it belongs to
ends. That is above the 2–8 s the plan's smoke test expected, which assumed
the 0.73 RTF of the batch path (§3.2); the measured RTF was closer to 0.95.

When transcription falls behind, nothing is dropped. The segmenter awaits
each row inline, so frames that arrive while a segment is being transcribed
queue in the server's socket buffer and then in the browser's
`bufferedAmount`, and the lag grows for the rest of the meeting rather than
settling. §5.2's "audio is what is lost" does not hold — with no ring buffer
there is nothing to discard from. The follow-up is a bounded segment queue
that drops its oldest segment, so that a busy CPU costs a stretch of the
live view rather than the rest of it.

The `MediaRecorder` starts before `ready`, not together with the worklet as
§4.1 says: it is started as soon as the socket is opened, so a refused or
slow hello never costs audio. Only the worklet waits for `ready`.

`anyio` was declared in `modules/audio/pyproject.toml`. It was already
installed transitively through `starlette`, so §1's "no new dependencies" is
true of the lockfile but not of the manifest; the module now names what it
imports.
