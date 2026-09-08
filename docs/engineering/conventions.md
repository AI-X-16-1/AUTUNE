# Code Conventions

Consistency here is what lets one person read another's module without a guide,
and what lets an AI agent work in an unfamiliar module without inventing
structure.

## Python

**Version:** 3.12. **Lint and format:** ruff. **Types:** mypy.

- Type-annotate every function signature. `Any` needs a comment explaining why.
- Prefer Pydantic models over dicts for anything structured.
- Modern typing: `list[str]`, `str | None`, no `typing.List`, no `Optional`.
- Absolute imports only.
- Small functions. A function that does not fit on a screen usually holds two
  responsibilities.

## Naming

| Thing | Style | Example |
| --- | --- | --- |
| Python package | `autune_<module>` | `autune_gap` |
| Python module, function, variable | `snake_case` | `extract_topics` |
| Class | `PascalCase` | `TopicGraphBuilder` |
| Constant | `UPPER_SNAKE` | `DEFAULT_RISK_THRESHOLD` |
| DB table | `<prefix>_<plural>` | `gap_topics` |
| DB column | `snake_case` | `risk_score` |
| Celery task | `autune.<module>.<verb>` | `autune.gap.on_transcript_ready` |
| Event | `autune.<producer>.<past-tense noun>` | `autune.transcript.ready` |
| API route | `/api/<module>/<plural-noun>` | `/api/gap/reports` |
| Env var | `AUTUNE_<MODULE>_<NAME>` | `AUTUNE_GAP_RISK_THRESHOLD` |
| TS component | `PascalCase` | `GapReportCard` |
| TS hook | `useSomething` | `useGapReport` |

Domain terms come from `../product/glossary.md`. Do not invent a synonym for a
term that already exists.

## Module file layout

Keep the standard shape (`../architecture/monorepo.md`). Responsibilities:

| File | Contains | Does not contain |
| --- | --- | --- |
| `router.py` | FastAPI routes, request/response schemas, auth, validation | Business logic, model inference |
| `tasks.py` | Celery task definitions, event publishing | Business logic |
| `slack.py` | `register(app)` attaching Bolt handlers | Business logic |
| `service.py` | Business logic, orchestration, persistence | HTTP concerns, Celery concerns |
| `pipeline.py` | Model loading and inference | Database writes, HTTP |
| `models.py` | SQLAlchemy tables | Business logic |
| `schemas.py` | Internal Pydantic models | Anything another module needs — that belongs in `packages/contracts` |
| `config.py` | Settings via `autune_core.settings` | Hardcoded values |

`router.py` and `tasks.py` are entry points: they parse, call `service.py`, and
format the result. Logic there cannot be reused by the other entry point.

## API

- Router prefix comes from `apps/api` (`/api/<module>`). Declare paths relative
  to it: `@router.get("/reports/{meeting_id}")`.
- Plural nouns for collections. Verbs only for genuine actions
  (`/reports/{id}/regenerate`).
- Response models are always explicit Pydantic models. Never return a raw dict
  or an ORM object.
- Long work returns `202` with a job ID; it never blocks the request.
- Errors use `autune_core.errors`:

```python
raise NotFoundError("meeting", meeting_id)
raise ValidationError("due_date could not be parsed", field="due_date")
```

Which produces a consistent body:

```json
{"error": {"code": "not_found", "message": "meeting mtg_001 not found", "details": {}}}
```

Never put transcript text, a file path, or personal data in an error message —
error strings reach external error tracking.

## Logging

Structured logging through `autune_core.logging`:

```python
log.info("gap_report_generated", meeting_id=meeting_id, gap_count=len(gaps))
```

- Event name first, as a lowercase snake_case string; context as keyword
  arguments.
- Log IDs, counts, durations, model versions, and scores.
- **Never log transcript text, audio paths, or personal data — at any level,
  including DEBUG.** See `../architecture/privacy.md`.

## Configuration

All configuration comes from `autune_core.settings`, which reads environment
variables. No magic numbers in the code path.

```python
class GapSettings(BaseSettings):
    risk_threshold: float = 0.7
    model_config = SettingsConfigDict(env_prefix="AUTUNE_GAP_")
```

Document every new variable in `environments.md` and add it to
`.env.example`.

## ML models

- Pin model versions explicitly. Never load "latest".
- Load models once at worker startup, not per task.
- Record the model version in the output row so results are traceable.
- Keep weights out of git. Download at build or first run into a cached
  directory.

## Frontend

**Node 22, pnpm, Next.js App Router, Tailwind, TypeScript strict.**

Before writing any UI, read `../design/ui-spec.md` and
`../design/design-tokens.json`. Every color, size, radius, control height and
motion value comes from the token file — do not introduce a value that is not
there. If a design needs one, it is a token change, not a local style.

```
apps/web/src/
├── app/                # Routing only. Pages compose features; no logic here.
│   ├── globals.css     # Tailwind entry + @theme mapping
│   └── tokens.css      # GENERATED from design-tokens.json — never edit
├── features/<module>/  # Owned by that module's owner
│   ├── CLAUDE.md       # That feature's rules and owner
│   ├── components/
│   ├── hooks/
│   ├── api.ts          # Calls to /api/<module>
│   └── types.ts        # Re-exports generated contract types
└── shared/             # ui/, api/, lib/ — team-owned
```

- A feature folder never imports from another feature folder. Enforced by
  eslint `no-restricted-imports`, the frontend counterpart of import-linter.
- **Every colour, size, radius, control height and motion value is a token.**
  `src/app/tokens.css` is generated from `docs/design/design-tokens.json` by
  `pnpm run gen:tokens`; CI fails if the committed file is stale. A value that
  is not a token is a token change, not a local style.
- Shared components come from `@/shared/ui` and are named in
  `../design/ui-spec.md` section 2. Do not build a second version of one.
- Types for API payloads come from generated contract types
  (`../architecture/contracts.md`). Never hand-write a type that mirrors a
  Pydantic model.
- Server components by default; `"use client"` only where interactivity requires
  it.
- Tailwind utilities in markup; extract a component rather than a CSS class when
  a pattern repeats.
- User-facing copy is Korean. Code, comments, and identifiers stay English.
- Component names come from `../design/ui-spec.md` section 2 (`StatusDot`,
  `Row`, `Band`, `ScoreLabel`, `ChipToggle`, `PiiToken`, `RecordingFrame`,
  `Waveform`). Do not invent a second name for one of these.

### What is genuinely shared in the frontend

Unlike the backend, `apps/web` is one application, so two files are shared by
all five owners and need a word in Slack before you change them:

- `src/app/layout.tsx` and the app shell — the sidebar, top bar and theme root.
- `apps/web/package.json` — one manifest for everyone's JS dependencies, where
  the backend gives each module its own `pyproject.toml`. Adding a dependency is
  rare after the first week; when it happens, say so, and regenerate the
  lockfile rather than merging it.

Routes under `src/app/` are per-screen files, so two people adding two screens
add two different files. That is not a conflict.

## Comments

Explain why, not what. A comment restating the code is noise; a comment
explaining a threshold, a workaround, or a non-obvious ordering constraint is
valuable.

```python
# Cross-encoder re-ranking is expensive, so retrieve 50 with BM25+SBERT
# and re-rank only the top 10. Recall past 10 was under 2% in W2 evaluation.
```
