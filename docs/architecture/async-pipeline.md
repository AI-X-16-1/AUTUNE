# Async Pipeline

Celery over Redis orchestrates the AI pipeline. It is also the only runtime
coupling permitted between modules.

## Flow

```
upload
  │
  ▼
autune.audio.process_recording          (A)
  │  STT → diarization → identification → PII masking
  │  → delete raw audio → write utterances
  ▼
publish  autune.transcript.ready        (TranscriptReady)
  │
  ├────────────────┬────────────────┐
  ▼                ▼                ▼
extraction (B)   gap (C)     context (D): topic linking   ← parallel
  │                │                │
  ├────────────────┼────────────────┤
  │                │                │
  ▼                │                │
autune.extraction.completed         │
  │                │                │
  ├──→ context (D): decision lineage │      ← D's second entry point
  │                │                │
  ▼                ▼                ▼
              autune.gap.completed  autune.context.completed
  │                │                │
  └────────────────┼────────────────┘
                   ▼
         autune.intelligence.aggregate  (E)
                   │
                   ▼
         autune.intelligence.completed
```

B and C do not depend on anything but A. D is the one exception, and only in
half of its work: **topic linking** needs only the transcript and runs in
parallel, while **decision lineage** needs the decisions B extracted and runs
after `autune.extraction.completed`.

D publishes `ContextLinks` once both halves are in, or — if B never reports —
with an empty `decision_lineage` and `"extraction"` in `missing_sources`. A
failure in B must not cost the user their topic links. See
`contracts.md`, "The B → D boundary".

E aggregates whatever has arrived; see "E and partial results" below.

## Naming

Task names are `autune.<module>.<verb>`. Event names are `autune.<noun>.<past
tense>` — `autune.transcript.ready`, `autune.extraction.completed`.

**An event name does not contain its producer.** `autune.transcript.ready` is
produced by A, and `audio` is nowhere in it. That is not an inconsistency to fix
later: the name says what happened, and who published it is exactly the thing a
consumer must not depend on.

### Subscribing

A consuming task is named by a mechanical transformation of the event: drop the
`autune.` prefix, join what is left with underscores, prefix `on_`.

```
autune.transcript.ready      ->  autune.<consumer>.on_transcript_ready
autune.extraction.completed  ->  autune.<consumer>.on_extraction_completed
```

`autune_core.publish` finds every registered task whose name ends that way, so
**subscribing is defining the task and unsubscribing is deleting it.** There is
no list of consumers anywhere, and a producer never names one.

Read the rule mechanically, not by meaning. "The producer's name, then the noun"
describes four of the five events and breaks on the first one, and following it
would produce `autune.audio.transcript_ready` next time — a name neither reading
agrees on.

| Name | Kind | Owner |
| --- | --- | --- |
| `autune.audio.process_recording` | task | A |
| `autune.audio.periodic.sweep_orphans` | periodic task | A |
| `autune.transcript.ready` | event | A |
| `autune.extraction.on_transcript_ready` | task | B |
| `autune.extraction.completed` | event | B |
| `autune.gap.on_transcript_ready` | task | C |
| `autune.gap.completed` | event | C |
| `autune.context.on_transcript_ready` | task | D |
| `autune.context.on_extraction_completed` | task | D |
| `autune.context.publish_if_ready` | task | D |
| `autune.context.completed` | event | D |
| `autune.intelligence.aggregate` | task | E |
| `autune.intelligence.completed` | event | E |

Register tasks in your module's `tasks.py`. `apps/worker` discovers them by
iterating the module list — never add your module to a registration block by
hand.

### Scheduling

A task that runs on a clock rather than on an event is named
`autune.<module>.periodic.<name>` and declares its period beside itself:

```python
from datetime import timedelta

from celery import shared_task

from autune_core import periodic


@shared_task(name="autune.audio.periodic.sweep_orphans")
@periodic(timedelta(hours=1))
def sweep_orphans() -> None:
    ...
```

`make_celery_app` reads the task registry and builds Celery's `beat_schedule`
from it, so **scheduling is defining the task and unscheduling is deleting
it** — the same property subscribing has, for the same reason: `apps/` is
assembly only and a schedule you append to is a registration block (invariant
6). Nothing under `apps/` changes when your module wants a scheduled job.

The name keeps `autune.<module>.` in front, so `TASK_ROUTES` already sends it
to your module's queue; a periodic task needs no new route.

Two rules for whoever writes one:

- **It must be safe to run twice and safe to overlap.** Beat restarts, and the
  previous run may not have finished when the next one is due. Celery
  serialises neither, and neither does this mechanism — it is the task author's
  responsibility, exactly as idempotency is.
- **Declare the schedule in UTC**, because the app runs `enable_utc`. KST is
  UTC+9, so `crontab(hour=0, minute=0)` fires at **09:00 KST**, and Monday
  09:00 KST is `crontab(day_of_week="mon", hour=0, minute=0)`. Convert once,
  here; do not change the app's timezone to avoid the arithmetic.

A periodic task takes no arguments — nothing calls it, so there is nothing to
pass — and reads what it needs inside the run. A task named
`autune.<module>.periodic.*` with no `@periodic`, or a `@periodic` under a name
nothing scans, fails the worker at startup instead of quietly never running.

## Queues

| Queue | Workload | Notes |
| --- | --- | --- |
| `gpu` | A's STT and diarization | GPU instance, low concurrency |
| `cpu_heavy` | B, C, D model inference | Higher concurrency |
| `default` | Integrations, notifications, aggregation | Fast, chatty |

Route with `TASK_ROUTES` in `autune_core.celery_app`, the one place the routes
exist. A long task on `default` blocks Slack notifications; a short task on
`gpu` wastes an expensive worker.

## One app, every process

`autune_core.celery_app.make_celery_app` builds the app and makes it the one
`celery.current_app` returns — in the calling thread and, via `set_default`, in
every other thread. `apps/worker` calls it with `include_tasks=True` and gets
the full registry; `apps/api` calls it with `include_tasks=False` and gets a
client that sends by task name with the same broker and routes, without
importing a single `tasks.py` (#258).

Before this, the API process had no app, `current_app` was Celery's built-in
default with a broker nobody runs, and an upload returned 202 into nothing.

A client's registry is empty, so `publish` from the API process finds no
subscribers. Publishing from a request is not a supported path today; the
request enqueues its own module's task by name, and the worker publishes.

## Payloads

**Send the contract, not the object.** Every event payload is a
`packages/contracts` model dumped to JSON. Never send an ORM instance, a numpy
array, a tensor, or a file path to something in local storage.

```python
# publishing
from autune_contracts import TRANSCRIPT_READY, TranscriptReady
from autune_core import publish

publish(TRANSCRIPT_READY, TranscriptReady(...).model_dump(mode="json"))


# consuming
from celery import shared_task


@shared_task(name="autune.extraction.on_transcript_ready", acks_late=True)
def on_transcript_ready(payload: dict) -> None:
    transcript = TranscriptReady.model_validate(payload)
```

Publish the **event name**, never a consumer's task name. `send_task(
"autune.extraction.on_transcript_ready", ...)` inside module A puts B's task name
in A's source; import-linter cannot see it, because it is a string. And
`autune.extraction.completed` already has two consumers, so B's source would
carry D's and E's names — after which adding a third means editing B, which
belongs to somebody else (invariant 10).

Import the constant rather than typing the string. `publish` refuses a name that
is not declared in `autune_contracts.events`, because a mis-typed event resolves
to a suffix nobody registered: nothing is sent, nothing raises, and the meeting
is simply never analysed.

Publishing to an event nobody consumes logs a warning and succeeds. C not being
deployed yet must not fail B, and the last event in the pipeline has no
consumers by design. `apps/worker/tests/test_registration.py` is what tells that
apart from a typo in a consumer's name — it checks both directions, every event
reaching a task and every `on_*` task reaching an event.

Fan-out is not atomic. With two subscribers the worker can die between the two
sends, which is one of the reasons consuming tasks must be idempotent.

Payloads are small — IDs and structured results. If a payload approaches
hundreds of kilobytes, pass IDs and let the consumer read the shared entities
from PostgreSQL instead.

## Idempotency

Every task must be safe to run twice. Celery retries, workers die mid-task, and
a meeting can be reprocessed after a correction.

- Key writes by `(meeting_id, module)` and upsert rather than insert.
- Delete-then-insert within one transaction is acceptable for derived results.
- Never append to a table without a uniqueness guard on the natural key.

## Retries and failure

```python
@celery_app.task(
    name="autune.gap.on_transcript_ready",
    autoretry_for=(TransientError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
```

- Retry transient failures (network, external API rate limits, GPU OOM).
- Do not retry validation failures — a malformed contract will not become valid.
- `acks_late=True` so a killed worker's task is redelivered.
- A failed module does not fail the others. If C crashes, B and D still finish
  and E aggregates what it has.

## E and partial results

E aggregates B, C, and D. It must handle the case where one of them has not
arrived. Two mechanisms:

1. **Completion tracking.** Each of B, C, D records completion for a meeting; E
   runs when all three are present, or when a timeout elapses.
2. **Timeout aggregation.** After a timeout (10 minutes by default), E produces
   a snapshot from whatever is available and marks the missing sources.

Never block a user-visible result waiting for a module that has failed.

## Progress and status

Users watch a progress bar during processing. Report progress through
`autune_core`'s status helper, not by writing custom rows:

```python
from autune_core.progress import report

report(meeting_id, stage="diarization", percent=45)
```

## Privacy in the pipeline

- The raw recording exists only inside A's task, in a temp path, and is deleted
  in a `finally` block before the task returns — including on failure.
- No task argument, event payload, log line, or error message may contain raw
  audio, a durable path to it, or unmasked transcript text.
- Celery results carry IDs and status, never transcript content.

See `privacy.md`. These are enforced in code review and in tests.

## Local development

```bash
docker compose up -d          # postgres (pgvector), redis
uv run celery -A autune_worker.celery_app worker -Q default,cpu_heavy,gpu -l info
uv run celery -A autune_worker.celery_app beat -l info        # only if you need a schedule
```

**Beat is exactly one process, and not one of the workers.** It is a clock, not
a consumer: it sends the scheduled task and whichever worker services the queue
runs it. Embedding it in a worker (`celery worker -B`) makes the number of
clocks the number of workers, so every scheduled job runs once per worker — the
reason Celery documents `-B` as a development convenience only. Scale workers
freely; there is one beat. Locally it is optional: nothing in the demo path
needs it, and module A's orphan sweep also runs at the head of every
`process_recording` for exactly that reason.

It is deliberately not a service in `infra/docker-compose.yml`: that file runs
postgres and redis only, the worker is started by hand, and beat alone in
compose would be the odd one out.

`gpu` is a queue name, not a hardware requirement — `task_routes` sends every
`autune.audio.*` task there regardless of `AUTUNE_AUDIO_DEVICE`. A worker that
doesn't service it never runs an audio task at all; it just sits in Redis with
no error. Set `AUTUNE_AUDIO_DEVICE=cpu` for whisper.cpp on CPU — that picks
what Whisper runs on, not which queue the task lands in. See
`../engineering/environments.md`.
