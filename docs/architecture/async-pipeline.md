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

Task names are `autune.<module>.<verb>`. Event names are
`autune.<producer>.<noun>` in the past tense.

| Name | Kind | Owner |
| --- | --- | --- |
| `autune.audio.process_recording` | task | A |
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

## Queues

| Queue | Workload | Notes |
| --- | --- | --- |
| `gpu` | A's STT and diarization | GPU instance, low concurrency |
| `cpu_heavy` | B, C, D model inference | Higher concurrency |
| `default` | Integrations, notifications, aggregation | Fast, chatty |

Route with `task_routes` in `apps/worker`. A long task on `default` blocks
Slack notifications; a short task on `gpu` wastes an expensive worker.

## Payloads

**Send the contract, not the object.** Every event payload is a
`packages/contracts` model dumped to JSON. Never send an ORM instance, a numpy
array, a tensor, or a file path to something in local storage.

```python
# publishing
from autune_contracts import TranscriptReady

payload = TranscriptReady(...).model_dump(mode="json")
celery_app.send_task("autune.extraction.on_transcript_ready", args=[payload])


# consuming
@celery_app.task(name="autune.extraction.on_transcript_ready")
def on_transcript_ready(payload: dict) -> None:
    transcript = TranscriptReady.model_validate(payload)
```

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
docker compose up -d          # postgres (pgvector), redis, neo4j
uv run celery -A apps.worker.celery_app worker -Q default,cpu_heavy -l info
```

Run the `gpu` queue only if you have a GPU; otherwise A falls back to
whisper.cpp on CPU. See `../engineering/environments.md`.
