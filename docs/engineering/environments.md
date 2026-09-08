# Environments and Local Setup

## Prerequisites

- Python 3.12 (`.python-version`)
- Node 22 (`.nvmrc`)
- uv, pnpm 9
- Docker and Docker Compose
- Optional: an NVIDIA GPU for module A. Without one, A falls back to
  whisper.cpp on CPU.

## First run

```bash
git clone <repo> && cd autune

git config core.hooksPath .githooks   # refuse accidental pushes to main

cp .env.example .env             # then fill in the secrets you need

docker compose -f infra/docker-compose.yml up -d
uv sync
pnpm install

uv run alembic -c infra/alembic.ini upgrade heads

uv run uvicorn apps.api.main:app --reload            # API   :8000
uv run celery -A apps.worker.celery_app worker -Q default,cpu_heavy -l info
pnpm --filter @autune/web dev                        # web   :3000
```

Working on one module only? `uv sync --package autune-gap` installs just that
module's dependencies and skips several gigabytes of ML wheels.

## Services

| Service | Port | Purpose | Who needs it |
| --- | --- | --- | --- |
| PostgreSQL | 5432 | Shared entities and all module tables | Everyone |
| Redis | 6379 | Celery broker and result backend | Everyone |
| Neo4j | 7474 / 7687 | Topic graph, decision lineage | C, D |
| Chroma | 8001 | Embeddings for retrieval | D |

```bash
docker compose -f infra/docker-compose.yml up -d postgres redis   # minimal
```

## Environment variables

Everything is read through `autune_core.settings`. Module settings use the
prefix `AUTUNE_<MODULE>_`.

### Shared

| Variable | Example | Notes |
| --- | --- | --- |
| `AUTUNE_ENV` | `local` | `local`, `staging`, `production` |
| `AUTUNE_DATABASE_URL` | `postgresql+psycopg://autune:autune@localhost:5432/autune` | |
| `AUTUNE_REDIS_URL` | `redis://localhost:6379/0` | |
| `AUTUNE_NEO4J_URI` | `bolt://localhost:7687` | C, D |
| `AUTUNE_NEO4J_USER` / `AUTUNE_NEO4J_PASSWORD` | | C, D |
| `AUTUNE_CHROMA_URL` | `http://localhost:8001` | D |
| `AUTUNE_SECRET_KEY` | | JWT signing. Never commit |
| `AUTUNE_LOG_LEVEL` | `INFO` | |
| `AUTUNE_RETENTION_DAYS` | `90` | Default analysis retention |

### Integrations

| Variable | Used by |
| --- | --- |
| `AUTUNE_SLACK_BOT_TOKEN`, `AUTUNE_SLACK_SIGNING_SECRET` | `apps/bot`, all modules that notify |
| `AUTUNE_NOTION_TOKEN`, `AUTUNE_NOTION_DATABASE_ID` | B |
| `AUTUNE_JIRA_URL`, `AUTUNE_JIRA_EMAIL`, `AUTUNE_JIRA_TOKEN` | B |
| `AUTUNE_GOOGLE_CALENDAR_CREDENTIALS` | D, and Phase 2 proactive agent |
| `AUTUNE_LLM_API_KEY` | Any module using an LLM |

### Module-specific

| Variable | Module | Meaning |
| --- | --- | --- |
| `AUTUNE_AUDIO_WHISPER_MODEL` | A | e.g. `large-v3` |
| `AUTUNE_AUDIO_DEVICE` | A | `cuda` or `cpu` |
| `AUTUNE_AUDIO_TEMP_DIR` | A | Where the recording lives during processing, and only then |
| `AUTUNE_GAP_RISK_THRESHOLD` | C | Default `0.7` |
| `AUTUNE_CONTEXT_RERANK_TOP_K` | D | Default `10` |

Every new variable goes into `.env.example` with a comment and into this table.
A variable that exists only in someone's local `.env` will break the next
person's setup.

## Secrets

- `.env` is gitignored. `.env.example` holds names and dummy values only.
- Never commit a token, key, or credential — including in a test fixture or a
  docstring.
- Staging and production secrets come from the deployment platform, never from
  a file in the repository.
- A leaked key is rotated immediately, not after the demo.

## Model weights

Weights are not in git. They download on first run into a cached directory:

```
AUTUNE_MODEL_CACHE=~/.cache/autune/models
```

Pin versions explicitly in code (`../engineering/conventions.md`). Pyannote
models need a Hugging Face token with the model licenses accepted:
`AUTUNE_HF_TOKEN`.

## Local privacy hygiene

`AUTUNE_AUDIO_TEMP_DIR` holds real audio while a task runs. It is gitignored and
cleared at the end of every task. Do not point it at a synced folder, and do not
keep test recordings of real meetings on disk. See
`../architecture/privacy.md`.

## Environments

| Environment | Purpose | Data |
| --- | --- | --- |
| local | Development | Synthetic fixtures only |
| staging | Integration and internal beta | Real meetings from consenting team members |
| production | Beta users | Real customer data, full retention policy enforced |

Never copy production data into staging or local. If you need a realistic
transcript, generate one.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `alembic upgrade head` errors about multiple heads | Use `heads`, plural. See `migrations.md` |
| Import error for `autune_core` | `uv sync` was not run, or the module is missing from the workspace members list |
| Celery task never runs | Worker is not listening on that queue. Check `-Q` |
| import-linter fails | You imported another module. Fix the import, not the config |
| Whisper is very slow | Running on CPU. Set `AUTUNE_AUDIO_DEVICE=cuda` or use a smaller model locally |
| Generated TS types are stale in CI | Run `pnpm run gen:contracts` and commit the output |
