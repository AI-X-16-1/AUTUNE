# Monorepo Structure

## Principle

We do not split `frontend/` and `backend/`. We use a **role-based three-layer
structure**. The goal is not a tidy folder tree — it is that five people never
need to edit the same file in the same week.

| Layer | Folder | Character | Ownership |
| --- | --- | --- | --- |
| Shared | `packages/` | Everyone depends on it, it rarely changes | Team consensus |
| Owned | `modules/` | Each person touches only their own | One owner per module |
| Assembly | `apps/` | Thin shells, no logic | Shared, almost never edited |

The rule that follows from this: **the amount of code you can write without
coordinating with anyone is maximized inside `modules/`.** If a task pushes you
into `packages/` or `apps/`, it is a coordination task, not a solo task.

## Layout

```
autune/
├── packages/
│   ├── contracts/         # Inter-module data contracts (Pydantic → TS types)
│   ├── core/              # DB session, settings, auth, logging, shared entities
│   └── integrations/      # Notion / Google Calendar / Slack SDK wrappers
│
├── modules/
│   ├── audio/             # A  Audio pipeline (Whisper, Pyannote, PII masking)
│   ├── extraction/        # B  Structured extraction (DeBERTa, NLI)
│   ├── gap/               # C  Gap detection (spaCy, NetworkX)
│   ├── context/           # D  Meeting context engine (SBERT, BM25, cross-encoder)
│   └── intelligence/      # E  Meeting intelligence (SetFit, XGBoost, Prophet)
│
├── apps/
│   ├── api/               # FastAPI — router auto-registration only
│   ├── worker/            # Celery — task auto-registration only
│   ├── web/               # Next.js + Tailwind (pnpm workspace)
│   └── bot/               # Slack Bolt for Python (uv workspace)
│
├── infra/                 # docker-compose, alembic, deployment
└── docs/
```

There is no `apps/extension`. The Chrome extension was dropped from the plan;
MVP input is recording-file upload, and the desktop app (Electron) is Phase 2.
When the desktop app is built it will live at `apps/desktop`.

## Module internals — identical for all five

```
modules/<name>/
├── CLAUDE.md               # Module rules for AI agents and new contributors
├── pyproject.toml          # This module's dependencies only
├── src/autune_<name>/
│   ├── __init__.py
│   ├── router.py           # APIRouter — collected automatically by apps/api
│   ├── tasks.py            # Celery tasks — collected automatically by apps/worker
│   ├── slack.py            # register(app) — collected automatically by apps/bot
│   ├── service.py          # Business logic
│   ├── pipeline.py         # AI pipeline
│   ├── models.py           # This module's tables (prefix mandatory)
│   ├── schemas.py          # Internal-only schemas
│   └── config.py           # Module settings
├── migrations/             # This module's own Alembic branch
└── tests/
```

Keep the file names. An agent or a teammate opening `modules/context/` should
find the same shape they saw in `modules/gap/`. When a file grows past a few
hundred lines, split it into a package (`service/` with focused submodules)
rather than inventing a new top-level name.

## Frontend mirrors the backend

```
apps/web/src/
├── app/                    # Routing only, as thin as possible
├── features/
│   ├── transcript/  (A)
│   ├── actions/     (B)
│   ├── gap/         (C)
│   ├── context/     (D)
│   └── dashboard/   (E)
└── shared/                 # UI primitives, API client, hooks
```

Cut along the same axis as the backend. One person owns `modules/gap/` and
`apps/web/src/features/gap/` together. `shared/` is team-owned, like
`packages/`.

## Conflict points and how each is handled

### 1. `apps/api/main.py` — router registration

Never hardcode. Iterate. After W1, nobody edits this file.

```python
MODULES = ["audio", "extraction", "gap", "context", "intelligence"]

for name in MODULES:
    router = import_module(f"autune_{name}.router").router
    app.include_router(router, prefix=f"/api/{name}", tags=[name])
```

Celery does the same: `include=[f"autune_{m}.tasks" for m in MODULES]`, and
`apps/bot` calls `autune_{m}.slack.register(app)` for each module.

`apps/web` is the only JavaScript app; `apps/bot` is Python, because Slack
integration uses Bolt for Python.

If you need custom registration behavior for your module, put the behavior in
your module's `router.py`, not in a special case in `main.py`.

### 2. Database migrations — separate Alembic branches

`down_revision` collisions are the most common merge conflict in a shared
Alembic history. Each module gets an independent chain.

```ini
# infra/alembic.ini
version_locations = modules/audio/migrations
                    modules/extraction/migrations
                    modules/gap/migrations
                    modules/context/migrations
                    modules/intelligence/migrations
```

Details and the runbook: `../engineering/migrations.md`.

### 3. Dependency files — workspaces

Python uses a **uv workspace**; JS uses a **pnpm workspace**. Each module owns
its own manifest.

```toml
# root pyproject.toml
[tool.uv.workspace]
members = ["packages/*", "modules/*", "apps/api", "apps/worker"]
```

Lockfile conflicts are regenerated, never hand-merged. Details:
`../engineering/dependencies.md`.

### 4. Direct imports between modules — forbidden

The most dangerous conflict class: it compiles, and someone else's code breaks.
See `module-boundaries.md`.

## Why this rather than frontend/backend

A `frontend/` + `backend/` split puts all five people in `backend/app/models.py`
and `backend/app/main.py` at once. Ownership becomes ambiguous exactly where
merge conflicts are worst. Splitting by role means the file most likely to
conflict — a module's own service code — has exactly one author.

The trade-off we accept: shared code is more expensive to change. That is
deliberate. `packages/contracts` should be hard to change; it is the interface
four modules depend on.

Recorded as `../decisions/0001-role-based-monorepo.md`.
