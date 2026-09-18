# Demo runbook — a recording in, a transcript on a screen

The whole path, one command at a time, with what to expect at each step and
what to look at when it does not happen. Written so the first end-to-end run
after the pending decisions land is a checklist rather than an afternoon.

```
token ──> meeting ──> consent ──> upload ──> queue ──> worker ──> /meetings/{id}
 main      #259        #283        #259      #258      main        main
```

The row under each step says where the code is. **Steps whose code is not on
`main` are marked, and the fallback in section 5 skips them.** Everything on
`main` in this document was run against `main` on 2026-09-18.

## 1. Before anything

| Need | Why | Check |
| --- | --- | --- |
| Docker | PostgreSQL (pgvector) and Redis | `docker compose -f infra/docker-compose.yml ps` shows both healthy |
| ffmpeg | Module A decodes with it; no ffmpeg, no upload | `ffmpeg -version` |
| `AUTUNE_AUDIO_HF_TOKEN` | pyannote is gated. The licence must be accepted on **three** repos — `speaker-diarization-3.1`, `segmentation-3.0`, `speaker-diarization-community-1` — or diarization fails partway through loading, naming a model you never asked for | `.env` has it |
| Whisper weights | `large-v3` downloads on first use, several GB | run the worker once, early, and wait |
| A recording | mp3 / wav / m4a, a few minutes, **people who have agreed to be the demo** | you have the file |
| `AUTUNE_ENV=local` | the token route and the dev page exist only under it | `.env` (it is the default) |

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

# 3 — web                                                                :3000
pnpm --filter @autune/web dev

# 4 — you
```

`autune_api.main:app`, not `apps.api.main:app`: the `apps/` directories are not
importable names, the packages inside them are (#228).

Expect from terminal 1:

```
{"event": "router_registered", "module": "audio", "prefix": "/api/audio"}
… five of those …
```

and `curl -s localhost:8000/health` returns `"env": "local"` and five modules.

## 3. The steps

### 3.1 A token — `main`

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

### 3.2 A meeting — **#259, not on `main`**

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

Until #259 merges, section 5.

### 3.3 Consent — **#283, not on `main`**

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

### 3.4 The upload — **#259, not on `main`**

```bash
curl -s -X POST localhost:8000/api/audio/meetings/$MEETING/recording -H "$AUTH" \
  -F "file=@/path/to/recording.m4a"
```

```json
{"meeting_id": "mtg_…", "status": "analyzing"}
```

202, and the response comes back before anything is transcribed. The file is
now in `AUTUNE_AUDIO_TEMP_DIR` waiting for the worker.

### 3.5 The queue — **#258, undecided; #275, undecided**

This is the step that does not work yet, and the reason is not module A's
code: the API process has no Celery app, so the enqueue goes to Celery's
default broker and not to Redis (#258). Separately, handing the worker a path
is the pattern `privacy.md` section 1 forbids by name, and #275 decides how
that is allowed. **Until both are resolved, 3.4 returns 202 and the worker
never hears about it.** Section 5.

When it does work, terminal 2 shows, in this order:

```
audio_process_started     meeting_id=mtg_…
audio_decoded             seconds=…  source_format=.m4a
transcript_masked         utterances=N  changed=M
audio_deleted             bytes=…
audio_process_finished    meeting_id=mtg_…  deleted=True  utterances=N  participants=K
event_published           event_name=autune.transcript.ready  subscribers=3
```

`deleted=True` before `event_published` is invariant 11 being kept; if the
order is ever different, stop.

### 3.6 The screen — `main`

Open `http://localhost:3000/meetings/$MEETING`.

- While the worker runs: "아직 전사된 내용이 없습니다". Utterances are written in
  one transaction at the end, so there is nothing to show part-way. Honest, not
  broken.
- After: rows with a time code, a speaker label (`SPEAKER_00`, …) and masked
  text. Phone numbers read `010-****-5678`.
- "실시간 보기" links to `/meetings/$MEETING/live` — S13 on fixture data, not a
  microphone. Say so if it is on screen.

The same thing over HTTP, for a terminal:

```bash
curl -s -H "$AUTH" localhost:8000/api/audio/transcripts/$MEETING | head -c 600
```

Without the header it is 403; that is the point of 3.1.

## 4. What the other modules should show

After `autune.transcript.ready` goes out, in terminal 2, from the other
modules:

| Module | Log to look for | Then |
| --- | --- | --- |
| B | `autune.extraction.on_transcript_ready` received, then `autune.extraction.completed` published | `GET /api/extraction/results/$MEETING` |
| C | received only — the pipeline is not wired to publish yet (`autune_gap/tasks.py`) | nothing, and that is expected |
| D | `autune.context.on_transcript_ready` … `autune.context.completed` | `GET /api/context/links/$MEETING` |
| E | records B and D, waits for C, aggregates on timeout | `GET /api/intelligence/scores/$MEETING` after the timeout |

If B's result is empty and 3.3 was skipped, that is why.

## 5. The fallback — until #258 and #275 land

Everything on `main` works if the meeting row exists and the task is called
directly in the worker's process. This is the demo that can be given today.

```bash
# The meeting row, by hand (POST /meetings is #259)
docker exec autune-postgres-1 psql -U autune -d autune -c \
  "INSERT INTO meetings (id, team_id, title, status, source, language,
   original_audio_deleted, pii_masked, created_at, updated_at)
   VALUES ('mtg_demo', '$TEAM', '데모 회의', 'analyzing', 'file_upload', 'ko',
   false, false, now(), now());"

# Consent, by hand (POST /consent is #283) — after the transcript is written,
# or every participant stays unconsented:
#   UPDATE participants SET consented = true WHERE meeting_id = 'mtg_demo';

# The task, in the same process the worker would run it in
uv run python -c "
from autune_audio.tasks import process_recording
process_recording('mtg_demo', '/path/to/recording.m4a')
"
```

Two things about that call:

- **It deletes the file.** That is what the task does (invariant 11). Point it
  at a copy.
- It loads Whisper and pyannote into the current process, so the first run is
  slow and the `HF_TOKEN` requirements above apply.

Then `http://localhost:3000/meetings/mtg_demo`, and the `UPDATE` above before
looking for B's results.

## 6. Where the first real run will break

Written down so the debugging starts from a list, not from nothing.

| Symptom | Likely cause |
| --- | --- |
| 3.1 works, 3.2 is 403 | The token's `team_id` is not the one in the body. Use the one the token route returned |
| 3.4 is 409 | The meeting is already `analyzing` — a previous upload, or the SQL insert in section 5 set it. Only `scheduled` and `failed` accept a recording |
| 3.4 is 413 after a long wait | 500 MB limit, enforced *after* Starlette has spooled the body (#265) |
| 3.4 is 202, terminal 2 is silent | #258. The enqueue did not reach Redis. Section 5 |
| Worker: `adopt` gets a path that does not exist | API and worker are on different filesystems. The task is handed a path (#275); they must share `AUTUNE_AUDIO_TEMP_DIR` |
| Worker: pyannote error naming `segmentation-3.0` | Licence accepted on one repo, not three |
| Worker: `ffmpeg is not installed` | It is not, in the worker's environment |
| Worker finishes, page shows rows, B's result is empty | 3.3 was skipped, or done after the run without the `UPDATE`. Consent is per meeting and must be there when participant rows are written |
| Worker finishes twice for one upload | `acks_late` redelivery after a long run. The second run dies at `adopt` and the meeting stays `complete` — expected |
| Page shows an error state | The browser has no token. `.env.local` needs a restart of terminal 3; `localStorage` does not |

## 7. Afterwards

```bash
# The recording is already gone; the transcript is not. If it was a real meeting:
docker exec autune-postgres-1 psql -U autune -d autune -c \
  "DELETE FROM meetings WHERE id = '$MEETING';"      # cascades through every module
rm -f apps/web/.env.local
```
