# Module Boundaries

## The rule

The five modules never import one another.

```
autune_audio   autune_extraction   autune_gap   autune_context   autune_intelligence
```

None of these may appear in another's import list — not at module level, not
inside a function, not through a re-export, not in a type-checking block, not in
a test.

They may import:
- `autune_contracts` — the shared data types
- `autune_core` — DB session, settings, auth, logging, shared entities
- `autune_integrations` — Slack, Notion, Jira, Google Calendar wrappers
- any third-party library declared in their own `pyproject.toml`

`packages/*` never imports `modules/*`. The dependency direction is one-way.

## Why this is the most important boundary

A cross-module import compiles, passes your tests, and breaks someone else's
work later. It creates:

- **Deploy coupling.** If B imports C, C cannot be deployed or refactored
  independently.
- **Model loading coupling.** Importing another module pulls in its ML model
  loading at import time. `apps/api` would load Whisper, DeBERTa, spaCy, SBERT,
  and XGBoost on startup.
- **Migration coupling.** Importing another module's `models.py` puts its tables
  into your metadata, and your migrations start emitting DDL for tables you do
  not own.
- **Ownership erosion.** Once one import exists, the boundary stops being real
  and the structure quietly collapses back into a shared codebase.

## The two permitted channels

### 1. Contract types — `packages/contracts`

Shared shapes. Pydantic models, no behavior, no I/O, no database access.
See `contracts.md`.

```python
from autune_contracts import TranscriptReady   # correct
from autune_audio.schemas import Transcript    # forbidden
```

### 2. Celery events

Runtime handoff. A publishes; B, C, D subscribe. Payload is always a contract
type serialized to JSON. See `async-pipeline.md`.

```python
# in autune_audio
publish("autune.transcript.ready", TranscriptReady(...).model_dump())

# in autune_extraction
@app.task(name="autune.extraction.on_transcript_ready")
def on_transcript_ready(payload: dict) -> None:
    transcript = TranscriptReady.model_validate(payload)
```

Reading another module's tables directly is a third channel, and it is also
forbidden. If you need data another module owns, it belongs in a contract.

## Enforcement

`import-linter` runs in CI on every pull request.

```toml
[[tool.importlinter.contracts]]
name = "Modules are independent"
type = "independence"
modules = ["autune_audio", "autune_extraction", "autune_gap",
           "autune_context", "autune_intelligence"]

[[tool.importlinter.contracts]]
name = "Packages do not depend on modules"
type = "forbidden"
source_modules = ["autune_contracts", "autune_core", "autune_integrations"]
forbidden_modules = ["autune_audio", "autune_extraction", "autune_gap",
                     "autune_context", "autune_intelligence"]
```

Run it locally before pushing:

```bash
uv run lint-imports
```

A failure here is never fixed by editing the import-linter config. It is fixed
by removing the import.

## Common situations and the correct move

| Situation | Wrong | Right |
| --- | --- | --- |
| C needs the transcript | `from autune_audio...` | Consume `TranscriptReady` from the event |
| E needs B's action items | Query `ext_action_items` | B publishes `ExtractionResult`; E stores what it needs in `intel_*` |
| Two modules need the same date-parsing helper | One imports the other | Duplicate it, or promote it to `packages/core` with team approval |
| D needs to know a meeting's participants | `from autune_audio.models import ...` | Read `Participant` from `autune_core` (shared entity) |
| B wants to trigger work in C | Call C's service function | Publish an event C subscribes to |
| You need one extra field from another module | Reach into its tables | Ask the owner to add an optional field to the contract |

## Duplication is cheaper than coupling

If two modules both need a small utility, duplicating twenty lines is the right
call. Promote to `packages/core` only when the third module needs it and the
behavior is genuinely stable — a shared helper needs team approval and breaks
five people when it is wrong.

## Frontend

The same rule applies in `apps/web`. A feature folder does not import from
another feature folder. Shared UI, hooks, and the API client live in
`src/shared/`.

```
features/gap/ → shared/          allowed
features/gap/ → features/actions/ forbidden
```
