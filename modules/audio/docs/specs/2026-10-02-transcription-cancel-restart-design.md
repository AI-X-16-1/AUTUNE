# Cancel and restart a transcription — design

Date: 2026-10-02. Module: A (`autune_audio`). Owner: 김민경.

## Why

A meeting enters `analyzing` when its recording is accepted and leaves it only
when `process_recording` completes or fails. Today nothing else can move it:

- **A worker that dies mid-job leaves the meeting stuck.** Celery redelivers
  the task (`acks_late`, `task_reject_on_worker_lost`), but `claim_job` declines
  any job that is not `queued`, so the redelivery does nothing. The job stays
  `running`, the meeting stays `analyzing`, and the screen polls forever. The dev
  server's deploys recreate the worker container, so this happens whenever a
  deploy lands during a transcription.
- **A wrong upload cannot be stopped.** The only way out is to wait for it to
  finish.
- **Re-uploading is not always possible.** A live-microphone meeting uploads
  the tab's `MediaRecorder` blob after `ended` and drops it once the upload
  succeeds (`useLiveSession.ts`), and live rows are never stored. When such a
  meeting gets stuck, the person has nothing to upload again.

The upload of a stuck `running` job is, however, still on the server:
`sweep_orphans` keeps a `queued` or `running` job's file for
`orphan_after_hours` (6 h). A restart can run from that file without keeping raw
audio any longer than the server already does.

## Scope

In scope: the transcription stages of module A — decoding, transcribing,
diarizing, masking, saving — for both uploaded and live meetings.

Out of scope:

- Re-running B, C, D or E after `TRANSCRIPT_READY` has gone out. That touches
  other modules (#194 is the re-run problem).
- Automatic resume of a stalled job. The design leaves the hook for it (see
  "Later").
- Saving the live recording to the person's own device. A separate, later
  piece of work: it changes what the screen promises about the original, so it
  needs its own privacy decision.
- An external STT provider. The cancel signal is built so a provider backend
  can use it; provider-side cancellation and cost are decided when one is added.

## Success criteria

1. A meeting stuck because its worker died can be run again from the same
   upload with one button, without uploading anything.
2. A transcription in progress can be cancelled; it stops, its upload is
   deleted, nothing is written to the transcript and nothing is published.
3. No path keeps raw audio longer than it is kept today.

## Decisions

- **Manual buttons, liveness by heartbeat** (option A of three). The worker
  writes a heartbeat; a job whose heartbeat is stale is *stalled*, and only a
  stalled job may be restarted. Progress alone cannot tell stalled from slow:
  diarization reports no progress (`stage_progress` is null) for minutes on a
  long meeting, and an external STT provider may report none at all. Restarting
  a live job would run one file twice, and whichever finished first would delete
  it under the other.
- **The person decides.** The server says a job is stalled; the person chooses
  restart or cancel. Cancel is available at any point while the meeting is
  `analyzing`.
- **Cancel by flag, not by `revoke`.** `revoke(terminate=True)` does nothing on
  the solo pool the Mac worker runs (#329) and would kill a task mid-write
  elsewhere. The worker stops itself at the next check.
- **No new meeting status.** `meetings.status` is a shared entity in
  `packages/core`. A cancelled meeting is `failed`, which already accepts a new
  upload; a restarted one stays `analyzing`.

## 1. Data model

`aud_jobs` (module A's table; one new revision on the `audio` Alembic branch):

| Change | Detail |
| --- | --- |
| `status` gains `cancelled` | `ck_aud_jobs_status` becomes `queued, running, done, failed, superseded, cancelled`. A cancelled job gets `finished_at`. |
| new column `heartbeat_at` | `timestamptz`, nullable. Written when the worker claims the job and every `heartbeat_interval_s` after. `claim_job` stamps it when it claims, so a long queue wait never reads as stalled. |

Job states:

```
queued ──claim──▶ running ──▶ done
  │                 │ ├──▶ failed      (exception)
  │                 │ └──▶ superseded  (restart, or a new upload)
  └──── cancel ─────┴────▶ cancelled
```

**Stalled is computed on read, not stored:**

```
stalled = status == 'running'
          AND coalesce(heartbeat_at, created_at) < now() - stall_after_s
```

A `queued` job is never stalled: one worker busy with an earlier meeting and a
lost message look the same from here. It can still be cancelled.

New settings in `autune_audio.config`, both provisional and recorded in
`HISTORY.md`: `heartbeat_interval_s = 30`, `stall_after_s = 120`.

**The six-hour clock runs from the upload, not from the latest attempt.**
`sweep_orphans` today keeps a `queued`/`running` job's file until the *job* is
older than `orphan_after_hours`. A restart makes a new job, so without a change
each restart would grant the file another six hours. The sweep instead measures
age as the earlier of the job's `created_at` and the file's mtime (a rename
keeps mtime). For a job that was never restarted the two are the same moment, so
today's behaviour does not change. `cancelled` joins `done`, `failed` and
`superseded` as an attempt that is over, whose file is collected at once.

## 2. API

All in `modules/audio` (router, service, schemas). No contract changes.

### `POST /api/audio/meetings/{meeting_id}/transcription/cancel` → 200 `MeetingState`

- Locks the meeting row, then `require_team_member`, as the upload does.
- Requires the meeting `analyzing` and its latest job `queued` or `running`
  (locked `FOR UPDATE`); otherwise 409 `nothing_to_cancel`.
- Sets the job `cancelled` with `finished_at`, the meeting `failed`, and
  commits.
- **Who deletes the upload** keeps "owned by exactly one party at a time"
  (privacy.md section 1):
  - a `running` job with a live worker: the worker, through `adopt`'s `finally`,
    once it sees the flag;
  - a `queued` job: the worker's `claim_job` declines it with
    `owns_file=True` and deletes it, as it does today for a finished job;
  - a **stalled** job: its owner is gone, so the API takes ownership and deletes
    the file in the request. The api and worker share the `audio-tmp` volume.
  - `sweep_orphans` stays the backstop for all three.

### `POST /api/audio/meetings/{meeting_id}/transcription/restart` → 202 `MeetingState`

- Same lock and membership check.
- Requires the latest job `running` and stalled; otherwise 409 `not_stalled`.
- Requires the meeting `analyzing`: a worker that died between `mark_complete`
  and `mark_published` leaves a stale `running` job on a delivered meeting.
- Requires the upload `{job_id}.upload` to exist and to be younger than
  `orphan_after_hours` by mtime; otherwise 409 `recording_gone`.
- Then the same sequence as the upload route: mark the old job `superseded`,
  create a new `queued` job, rename `{old}.upload` to `{new}.upload` (same
  directory, atomic), commit, enqueue. If the enqueue fails: `mark_failed` and
  delete the file, as the upload route does. The meeting stays `analyzing`.
- If the old worker was not dead after all, it finds its job `superseded` at its
  next check and stops; its `finally` deletes `{old}.upload`, which no longer
  exists (`_delete` is `missing_ok`).

### `GET /api/audio/meetings/{meeting_id}` — `MeetingDetail` gains

| Field | Meaning |
| --- | --- |
| `stalled: bool` | latest job `running` and stalled |
| `restartable: bool` | `stalled` and the upload passes the restart checks above |
| `cancellable: bool` | meeting `analyzing` and latest job `queued` or `running` |
| `cancelled: bool` | meeting `failed` because its latest job was cancelled, so the screen can say it was stopped rather than show an error |

The screen already polls this every 3 s; the buttons follow these flags so the
rule for showing them lives in one place.

## 3. Worker

### `JobGuard` (new `autune_audio/job_guard.py`)

- Started right after `claim_job` returns `run=True`; stopped and joined when
  the task ends, however it ends.
- A daemon thread that, every `heartbeat_interval_s`, runs in its own short
  session:
  `UPDATE aud_jobs SET heartbeat_at = now() WHERE id = :id RETURNING status`.
  The first write happens at start. If the status read back is not `running`,
  it sets a stop flag. One statement both proves the worker alive and reads
  whether it should stop.
- `guard.check()` raises `JobStopped(status)` once the flag is set.
- Called from:
  - `ProgressReporter.stage()` and `.update()`, so a cancel lands within about
    one heartbeat interval during recognition, which reports per segment;
  - before masking and before saving;
  - a future STT provider backend's wait loop, which is handed the same check.
- Diarization reports progress through pyannote's `hook`
  (`diarization._progress_hook`), so a cancel lands during diarization as well.
  The measured latency per stage is in `HISTORY.md`.

### The commit fence

The saving transaction first locks the job row `FOR UPDATE` and raises
`JobStopped` unless it is `running`, before `persist_transcript`. A cancel or
restart that lands after the last `check()` therefore rolls the whole write
back: no utterances, no embeddings, no `complete`, no publish. The cancel API
locks the same row, so the two serialise: if saving commits first, the meeting
is `complete` and the cancel gets 409; if the cancel commits first, saving
stops. Every path that locks both rows takes the meeting first, then the job.

### Exceptions

- `JobStopped` is handled apart from other exceptions: no `mark_failed`, no
  re-raise. The task logs `audio_process_stopped` with the job status and
  returns, so the message is acked.
- `mark_failed` already skips a job that is not `queued` or `running`, so a
  superseded attempt cannot flip the restarted meeting to `failed`. A test pins
  this.
- `adopt`'s `finally` deletes the upload as before.

### Unchanged

- `claim_job`: a `cancelled` job is declined and its file deleted by the
  existing branch.
- A redelivery after a worker died is still declined; the job shows as stalled
  and waits for the person. Automatic resume would hook in here.

## 4. Screen

`apps/web/src/features/transcript/`, the S12 `ProcessingStages` area. The S12
mockup has no cancel or restart, so these use existing tokens and components
only.

| State | Shows |
| --- | --- |
| `cancellable` | A secondary "처리 중단" under the stage list. Pressing it opens an inline confirmation in place (no `window.confirm`): "처리를 중단하면 서버에 있는 원본 녹음이 삭제됩니다.", with [중단하기] / [계속 진행]. |
| `stalled && restartable` | A notice above the list: "2분 넘게 처리 응답이 없습니다. 서버가 다시 시작되었을 수 있습니다."; [다시 시작] and [처리 중단]. |
| `stalled && !restartable` | The recording is no longer on the server, so it cannot restart; stop the processing and upload again; [처리 중단] only. The line also says a live recording may have no file to upload again: `meetings.source` does not tell a live meeting from an upload, so the sentence is shown to both. |
| `cancelled` | The existing failed view's re-upload path, with the line "처리를 중단했습니다. 원본 녹음은 삭제되었습니다." rather than the error line. |

Korean copy goes through a Korean-writing pass before it ships, so it does not
read translated; the shipped strings are in
`apps/web/src/features/transcript/components/TranscriptionControls.tsx`. A
refusal from the server is shown by its error code in Korean, never the
server's English message.

## 5. Testing

Backend, pytest against Postgres:

- Cancel from `queued`, from `running`, and from stalled: transitions, who
  deletes the file, 409 `nothing_to_cancel`, 403 for a non-member.
- Restart: rename, new job, old job `superseded`; 409 `not_stalled`; 409
  `recording_gone` for a missing file and for one past `orphan_after_hours`;
  rollback when the enqueue fails.
- Sweep: a restarted job's file is collected six hours after the upload, not
  six hours after the restart; `cancelled` files are collected at once.
- `JobGuard` with an injected short interval: heartbeat written, `check()`
  raises after the status changes, thread stops on exit.
- `process_recording` with fake transcriber and diarizer:
  - cancel during recognition → no utterances, no publish, file deleted,
    meeting `failed`;
  - superseded during recognition → meeting stays `analyzing`, `mark_failed`
    not applied;
  - cancel between the last check and saving → fence rolls back.
- The migration upgrades and downgrades.

Frontend, vitest: which buttons show for each flag combination; the inline
confirmation; `pnpm run build`.

Real run, isolated local stack from a worktree:

1. Upload, `kill -9` the worker during recognition, start it again, wait two
   minutes: the screen shows stalled; [다시 시작] runs the meeting to
   `complete`.
2. Upload, press 처리 중단 during recognition: the worker stops, the upload is
   gone from the temp directory, and no `autune.transcript.ready` is sent.

Heartbeat interval, stall threshold and the measured cancel latency per stage go
in `modules/audio/HISTORY.md`.

## Later

- **Automatic resume (option B).** A periodic task re-queues a stalled job once,
  using the restart path, and leaves a second stall to the person. Needs an
  attempt count on the job.
- **Saving the live recording locally.** Offer the `MediaRecorder` blob as a
  download when a live meeting ends. Needs a decision on what privacy.md and the
  "원본 처리 후 삭제" copy promise.
- **External STT provider.** Pass `guard.check` into its wait loop; decide
  provider-side cancel and the cost of a restart re-sending the audio.
