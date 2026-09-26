# Demo runbook — a recording in, a transcript on a screen

The whole path, one command at a time, with what to expect at each step and
what to look at when it does not happen. Written so an end-to-end run is a
checklist rather than an afternoon.

```
token ──> meeting ──> consent ──> upload ──> queue ──> worker ──> /meetings/{id}
```

**Everything in this path is on `main`** as of 2026-09-21 (#300 gave the API a
Celery app, #283 the consent route, #259 the upload and the job handover, #301
the browser screens). Two ways through it: **section 3 is the browser** — one
page, no `curl` — and **section 3b is the same path over HTTP**, for when a
step needs isolating. The whole path was run end to end from the browser on
2026-09-21 with a synthesized two-voice recording. What broke on the way is in
section 6, marked *seen*.

## 1. Before anything

| Need | Why | Check |
| --- | --- | --- |
| Docker | PostgreSQL (pgvector) and Redis | `docker compose -f infra/docker-compose.yml ps` shows both healthy |
| ffmpeg | Module A decodes with it; no ffmpeg, no upload | `ffmpeg -version` |
| `AUTUNE_AUDIO_HF_TOKEN` | pyannote is gated. The licence must be accepted on **three** repos — `speaker-diarization-3.1`, `segmentation-3.0`, `speaker-diarization-community-1` — or diarization fails partway through loading, naming a model you never asked for | `.env` has it |
| Whisper weights | `large-v3` downloads on first use, several GB | run the worker once, early, and wait |
| A recording | mp3 / wav / m4a, a few minutes, **people who have agreed to be the demo** | you have the file |
| `AUTUNE_ENV=local` | the token route and the dev page exist only under it | `.env` (it is the default) |
| `AUTUNE_CORS_ALLOWED_ORIGINS=http://localhost:3000` | the page sends `Authorization`, so the browser preflights, and the API answers a preflight only for listed origins (#241, opt-in) | `.env`. **Without it the page says "Failed to fetch"** and `curl` works fine — seen |

```bash
cp .env.example .env                                   # once; fill AUTUNE_AUDIO_HF_TOKEN
docker compose -f infra/docker-compose.yml up -d
uv sync --all-packages                                 # not plain `uv sync`
pnpm install
uv run alembic -c infra/alembic.ini upgrade heads      # plural: six branches
```

## 2. Four terminals

```bash
# 1 — API                                                                :8000
uv run uvicorn autune_api.main:app --reload

# 2 — worker, on every queue; audio tasks route to `gpu`
uv run celery -A autune_worker.celery_app worker -Q default,cpu_heavy,gpu -l info
#   Windows: add --pool=solo (README.md); the prefork pool keeps killing its children there

# 3 — web                                                                :3000
pnpm --filter @autune/web dev

# 4 — you
```

`autune_api.main:app`, not `apps.api.main:app`: the `apps/` directories are not
importable names, the packages inside them are (#228).

**No beat.** The demo needs no clock, and the one scheduled job there is today
— module A's orphan sweep — also runs at the head of every `process_recording`,
so a demo collects its own leftovers without one. If you do want it:

```bash
# 5 — beat, if you want the schedule. Exactly one, and never `worker -B`
uv run celery -A autune_worker.celery_app beat -l info
```

One process, separate from the workers. `celery worker -B` embeds the clock in
that worker instead, so the number of clocks becomes the number of workers you
happen to have started — two workers, and every scheduled job runs twice.
Celery's own documentation calls `-B` a development convenience for that reason.
Beat writes `celerybeat-schedule` in the working directory.

Expect from terminal 1 (console format under `AUTUNE_ENV=local`; JSON is the
non-local renderer):

```
[info     ] router_registered  module=audio  prefix=/api/audio
… five of those …
```

and `curl -s localhost:8000/health` returns `"env": "local"` and five modules.

## 3. The steps, from the browser

Do 3b.1 once (a token, into the browser), then:

1. Open `http://localhost:3000/meetings/new`.
2. Title. The team is picked for you — the page reads `/api/audio/teams` for
   the token's teams; if it says "속한 팀이 없습니다", the token is wrong or
   terminal 1 is not up.
3. Pick the recording (mp3 / wav / m4a, 500 MB). A wrong extension or size is
   refused under the dropzone before anything is sent.
4. **Tick the consent line.** The button stays disabled until you do, and that
   is the design: the page sends `POST /consent` *before* the upload, because
   B and C analyse only consented utterances (#190). Skip it and the demo shows
   a transcript and no action items.
5. "업로드하고 분석 시작". The page opens the meeting, attests, uploads, and
   lands on `/meetings/{id}` — screen S12, six stages, "분석 중".
6. Wait. The page polls the meeting every 3 s. Stages it cannot see say so
   ("단계별 진행률은 아직 제공되지 않아 회의 상태로 표시합니다"); per-stage
   progress needs a feed that does not exist. Terminal 2 is where the real
   progress is (3b.5).
7. When the worker finishes the page switches to the transcript on its own —
   no reload. Rows with a time code, a speaker label (`화자 1`, `화자 2`, …) and
   masked text.

If the run fails, S12 goes red and offers "다시 업로드", which goes back to
`/meetings/new?meeting={id}`: same meeting, new recording. A meeting that
finished refuses another upload (409), and the page shows the reason as-is.

### 3a. The live path, by hand

Open `http://localhost:3000/meetings/$MEETING/live` for a meeting that is
`scheduled` (3b.2 makes one). Tick the consent line — the button does not
wait for it; the spec says recording works without consent, and B and C then
analyse nothing — press 녹음 시작, allow the microphone. Speak, pause: a row
appears about four seconds after you stop talking (`large-v3-turbo`, ten CPU
threads — `docs/modules/audio-live-transcription.md` §9 has the numbers),
with a time code and no speaker — speakers come from the stored pipeline
after the upload. A phone number said aloud appears masked. 정지 sends the
last row, then uploads the whole recording through 3b.4 as a File and lands on
`/meetings/$MEETING`, where the worker's run replaces the live rows.

If the API is down when you press start, the screen says so and records
anyway; 정지 still uploads. If nothing happens after 녹음 시작, check
`AUTUNE_CORS_ALLOWED_ORIGINS` (section 1) — a socket is subject to the same
origin check as a fetch. The meeting stays at `recording` between 정지 and
the upload, and `start_transcription` accepts it there for that reason.

## 3b. The same steps over HTTP

### 3b.1 A token

```bash
curl -s -X POST localhost:8000/api/audio/dev/token \
  -H 'content-type: application/json' \
  -d '{"email": "you@example.com", "team_name": "Demo Team"}'
```

```json
{"token": "eyJ…", "user_id": "user_…", "team_id": "team_…"}
```

Keep all three. The same email returns the same ids every time; a reload does
not make a second team. Export for the rest of this file:

```bash
export TOKEN=eyJ…   TEAM=team_…
export AUTH="Authorization: Bearer $TOKEN"
```

And give it to the browser, once, before starting terminal 3:

```bash
echo "NEXT_PUBLIC_AUTUNE_DEV_TOKEN=$TOKEN" >> apps/web/.env.local
```

(Or, later, in the browser console: `localStorage.setItem("autune.token", "…")`
— that one wins, and needs no restart.)

### 3b.2 A meeting

```bash
curl -s -X POST localhost:8000/api/audio/meetings -H "$AUTH" \
  -H 'content-type: application/json' \
  -d "{\"title\": \"데모 회의\", \"team_id\": \"$TEAM\"}"
```

```json
{"meeting_id": "mtg_…", "status": "scheduled"}
```

```bash
export MEETING=mtg_…
```

`GET /api/audio/meetings/$MEETING` (same header) returns the title, the status
and the two privacy flags — what the page polls.

### 3b.3 Consent

```bash
curl -s -X POST localhost:8000/api/audio/meetings/$MEETING/consent -H "$AUTH" \
  -H 'content-type: application/json' -d '{"attested": true}'
```

```json
{"meeting_id": "mtg_…", "attested": true}
```

**Do this before the upload.** Without it the transcript is stored and modules
B and C analyse nothing — every participant row is `consented = false` and
both filter on `true`. That is the design, not a bug (#190); it is also the
most likely reason a demo shows a transcript and no action items.

### 3b.4 The upload

```bash
curl -s -X POST localhost:8000/api/audio/meetings/$MEETING/recording -H "$AUTH" \
  -F "file=@/path/to/recording.m4a"
```

```json
{"meeting_id": "mtg_…", "status": "analyzing"}
```

202, and the response comes back before anything is transcribed. The file is
now in `AUTUNE_AUDIO_TEMP_DIR` as `{job_id}.upload`, and the queue carries the
job id — never the path (`privacy.md` section 1, decision #275). Terminal 1
shows `audio_transcription_started` then `audio_process_queued`.

### 3b.5 The queue and the worker

Terminal 2 shows, in this order (copied from a real run, not from the code):

```
audio_process_started     job_id=job_…  meeting_id=mtg_…
audio_decoded             seconds=…  source_format=.upload
diarized                  speakers=K  turns=T
audio_deleted             bytes=0
transcript_masked         changed=M  meeting_id=mtg_…  utterances=N
transcript_persisted      audio_deleted=True  meeting_id=mtg_…  participants=K
event_published           event_name=autune.transcript.ready  subscribers=3
audio_process_finished    deleted=True  meeting_id=mtg_…  participants=K  utterances=N
```

**`audio_deleted` before `transcript_persisted`** is invariant 11 being kept:
the recording is gone before the first row is written. If that pair is ever
the other way round, stop. `bytes=0` is expected on this path — the adopted
file is not counted, only confirmed gone. `source_format=.upload` is expected
too: the file is named after the job and ffmpeg identifies the container from
the bytes.

Before all that you may see `audio_orphan_deleted` lines — the sweep that runs
at the start of every task, collecting uploads whose attempt is over. Normal.

### 3b.6 The screen

Open `http://localhost:3000/meetings/$MEETING`.

- While the worker runs: S12, "분석 중". After: the transcript. Section 3, steps
  6–7.
- Phone numbers read `010-****-5678`.
- "실시간 보기" links to `/meetings/$MEETING/live` — S13 on fixture data, not a
  microphone. Say so if it is on screen.

The same thing over HTTP, for a terminal:

```bash
curl -s -H "$AUTH" localhost:8000/api/audio/transcripts/$MEETING | head -c 600
```

Without the header it is 403; that is the point of 3b.1.

## 4. What the other modules should show

After `autune.transcript.ready` goes out, in terminal 2, from the other
modules:

| Module | Log to look for | Then |
| --- | --- | --- |
| B | `autune.extraction.on_transcript_ready` received, then `autune.extraction.completed` published | `GET /api/extraction/results/$MEETING` |
| C | `autune.gap.on_transcript_ready` received, then `autune.gap.completed` published | `GET /api/gap/reports/$MEETING`, or open `/meetings/$MEETING/gap` (S20). Once #284 lands the `curl` needs `-H "$AUTH"` |
| D | `autune.context.on_transcript_ready` … `autune.context.completed` | `GET /api/context/links/$MEETING` |
| E | records B, C and D and aggregates when all three have reported; the timeout is the fallback if one never does | `GET /api/intelligence/scores/$MEETING` |

If B's result is empty and the consent step (3, step 4 or 3b.3) was skipped, that is why.

### 4.1 Each of them needs configuration before it runs at all — seen

On the first end-to-end run every downstream task raised on its first
utterance, and E — which aggregates only after a first completion — therefore
did nothing. On the second, with B, C and D faked, E raised inside `aggregate`
itself. **With `.env.example` as-is, a real meeting produces module A's
output and nothing else.** None of it is a code bug; each module ships a model
it cannot find by default. The module owner's word on the right setting beats
this table, which is what the run showed:

| Module | Failed with | For a demo of the shape | For real output |
| --- | --- | --- | --- |
| B | `CLASSIFIER_IMPL=local needs AUTUNE_EXTRACTION_CLASSIFIER_CHECKPOINT` — no trained checkpoint is published | `AUTUNE_EXTRACTION_CLASSIFIER_IMPL=fake` | `…_IMPL=local`, `…_CHECKPOINT=<ckpt1>,<ckpt2>` (comma = ensemble, #245; the checkpoints are on B's machine, #112), `…_DEVICE=cpu`. Needs transformers, which `uv sync --all-packages` does not install: `uv run --with transformers celery …` or the `local-models` extra. First load ~60 s |
| B (step 4, #12) | `NLI_IMPL=local needs AUTUNE_EXTRACTION_NLI_CHECKPOINT`, or a 401/404 from a private HF Hub repo | `AUTUNE_EXTRACTION_NLI_IMPL=fake` | `.env.example`'s `…_NLI_CHECKPOINT` is #172's private checkpoint (`mminjae97/autune-context-kornli-klue-roberta`) — needs `hf auth login` with an invited account, or ask 문민재 for access. Same `local-models` extra as the classifier, no second install |
| C | `No module named 'spacy'` | `AUTUNE_GAP_NER_IMPL=fake` | `uv sync --package autune-gap --extra local-models` then `python -m spacy download ko_core_news_lg` |
| D | `embedder inference endpoint http://autune-embed.internal:8080 is not reachable` | `AUTUNE_CONTEXT_EMBEDDER_IMPL=fake`, `…_RERANKER_IMPL=fake`, `…_NLI_IMPL=fake` | `kure_v1_local` etc. with the `local-models` extra, or the `_ENDPOINT`s pointed at a running inference server |
| E | `the SetFit gap classifier needs the 'local-models' extra` — `aggregate` raises after B, C and D reported, so `/scores/{id}` is 404 and the dashboard counts nothing | `AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL=fake` | `uv sync --package autune-intelligence --extra local-models`; the SetFit head fits on first use |

`fake` implementations are deterministic stand-ins for tests. They make the
pipeline complete and the screens fill; they do not make the results mean
anything. Say which one the demo is using.

## 5. Running the task by hand

Not needed for the demo any more. Kept because it is the fastest way to put a
recording through the pipeline without the API, the queue or a browser — a
model change, a masking check.

```bash
# A meeting and a job, by hand
docker exec autune-postgres-1 psql -U autune -d autune -c \
  "INSERT INTO meetings (id, team_id, title, status, source, language,
   original_audio_deleted, pii_masked, created_at, updated_at)
   VALUES ('mtg_demo', '$TEAM', '데모 회의', 'analyzing', 'file_upload', 'ko',
   false, false, now(), now());
   INSERT INTO aud_jobs (id, meeting_id, status) VALUES ('job_demo', 'mtg_demo', 'queued');"

# The file where the worker will look for it
cp /path/to/recording.m4a "$AUTUNE_AUDIO_TEMP_DIR/job_demo.upload"

# The task, in the same process the worker would run it in
uv run python -c "
from autune_audio.tasks import process_recording
process_recording('job_demo')
"
```

- **It deletes the file.** That is what the task does (invariant 11). Copy,
  do not move.
- It loads Whisper and pyannote into the current process, so the first run is
  slow and the `HF_TOKEN` requirements above apply.
- Consent, if you want B and C to produce anything: `POST /consent` (3b.3)
  before the run — since #283 the attestation is read for every participant
  row written later — or `UPDATE participants SET consented = true WHERE
  meeting_id = 'mtg_demo'` after it.
- **This runs module A and nothing else.** The process has only A's tasks
  registered, so `publish` finds no subscribers, logs
  `event_no_subscribers`, and B, C, D, E never hear of the meeting. That is
  the difference between this and terminal 2, which imports all five. For
  the other modules, use the queue.
- Section 7's cleanup uses `$MEETING`; here the meeting is `mtg_demo`.

## 6. Where the first real run will break

Written down so the debugging starts from a list, not from nothing.

| Symptom | Likely cause |
| --- | --- |
| **Page says "Failed to fetch"; `curl` with the token works** — *seen* | CORS. The browser preflights because of `Authorization`, and the API answers `OPTIONS` with 405 unless `AUTUNE_CORS_ALLOWED_ORIGINS` lists the page's origin (section 1) |
| **A phone number is in the transcript in the clear** — *seen* | Whisper wrote `공일공 일이삼사 오육칠팔` as `010 -12345678`: a space and a hyphen, then eight digits run together. On `main` the separator class is one character, so no pattern matches and the storage guard — same patterns — passes it too. **#211 catches it.** Until #211 merges, treat any spoken number in a demo recording as unmasked |
| **Worker: B, C and D each raise on the first utterance; E never aggregates** — *seen* | Section 4.1. Not a bug; each needs a model it cannot find by default |
| Worker raises an `IntegrityError` on a meeting id you never created, seconds after starting — *seen* | A message left in the shared Redis by somebody else's earlier run. Harmless; `redis-cli FLUSHDB` on a dev box if it annoys |
| Worker log says `audio_deleted bytes=0` for a file that was not empty — *seen* | The adopted-file path does not count bytes. Cosmetic; the file is gone |
| 3b.1 works, 3b.2 is 403 | The token's `team_id` is not the one in the body. Use the one the token route returned |
| 3b.4 is 409 | The meeting is already `analyzing` — a previous upload, or the SQL insert in section 5 set it. Only `scheduled` and `failed` accept a recording |
| 3b.4 is 413 after a long wait | 500 MB limit, enforced *after* Starlette has spooled the body (#265) |
| 3b.4 is 500 "could not be queued", meeting `failed`, file gone — *seen* | The API could not reach the broker. Before #300 this was every upload; now it means Redis is down or `AUTUNE_REDIS_URL` differs between terminal 1 and 2. The file is deleted on purpose: a recording whose task does not exist has nobody to delete it |
| 3b.4 is 202, terminal 2 is silent | Terminal 2 is not listening on `gpu`, or is on a different Redis db than terminal 1 |
| Worker: `decode` fails on a file that does not exist | API and worker are on different filesystems. The worker rebuilds the path from the job id; both must share `AUTUNE_AUDIO_TEMP_DIR` |
| Worker: pyannote error naming `segmentation-3.0` | Licence accepted on one repo, not three |
| Worker: `ffmpeg is not installed` | It is not, in the worker's environment |
| Worker finishes, page shows rows, B's result is empty | 3b.3 was skipped (the consent line in the browser was not ticked), or done after the run without the `UPDATE`. Consent is per meeting and must be there when participant rows are written |
| Worker logs `audio_process_declined` for a job | `acks_late` redelivery after a long run. The second delivery is turned away at `claim_job` and the meeting stays `complete` — expected |
| Page says "속한 팀이 없습니다" | The browser's token is for a user on no team, or is stale. Redo 3b.1 |
| Page: transcript is one long row, one speaker — *seen* | The recording was synthesized (`say`); pyannote groups TTS voices as one speaker. Use a real recording for the demo |
| Page shows an error state | The browser has no token. `.env.local` needs a restart of terminal 3; `localStorage` does not |

## 7. Afterwards

```bash
# The recording is already gone; the transcript is not. If it was a real meeting:
docker exec autune-postgres-1 psql -U autune -d autune -c \
  "DELETE FROM meetings WHERE id = '$MEETING';"      # cascades through every module
rm -f apps/web/.env.local
```
