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

docker compose -f infra/docker-compose.yml up -d      # postgres (pgvector), redis
uv sync --all-packages
pnpm install

uv run alembic -c infra/alembic.ini upgrade heads

uv run uvicorn apps.api.main:app --reload            # API   :8000
uv run celery -A apps.worker.celery_app worker -Q default,cpu_heavy -l info
pnpm --filter @autune/web dev                        # web   :3000
uv run python -m autune_bot                          # Slack bot (socket mode)
```

`--all-packages` is not optional. The workspace root is virtual — it declares
`package = false` and no dependencies of its own — so a plain `uv sync` installs
the dev tooling and nothing else, and the first `import autune_core` fails.

Working on one module only? `uv sync --package autune-gap` installs just that
module's dependencies and skips several gigabytes of ML wheels. It brings in
`autune_core` and `autune_contracts` as well, because your module depends on
them, but not the other four modules — so the full test suite cannot run in that
environment. Run `uv sync --all-packages` before `uv run pytest`, or scope the
run to your own tests with `uv run pytest modules/gap`.

## Services

| Service | Port | Purpose | Who needs it |
| --- | --- | --- | --- |
| PostgreSQL | 5432 | Shared entities and all module tables | Everyone |
| Redis | 6379 | Celery broker and result backend | Everyone |

Two services, and everyone needs both. Embeddings, topic graphs and decision
lineage are all PostgreSQL rows — there is no vector database and no graph
database. The image is `pgvector/pgvector:pg16` rather than plain `postgres`;
the extension is enabled by a `packages/core` migration. See
`../decisions/0004-pgvector-over-chroma.md` and
`../decisions/0005-no-graph-database.md`.


## Environment variables

Everything is read through `autune_core.settings`. Module settings use the
prefix `AUTUNE_<MODULE>_`.

### Shared

| Variable | Example | Notes |
| --- | --- | --- |
| `AUTUNE_ENV` | `local` | `local`, `staging`, `production` |
| `AUTUNE_DATABASE_URL` | `postgresql+psycopg://autune:autune@localhost:5432/autune` | |
| `AUTUNE_REDIS_URL` | `redis://localhost:6379/0` | |
| `AUTUNE_SECRET_KEY` | | JWT signing. Never commit |
| `AUTUNE_ENCRYPTION_KEY` | | Encrypts team integration credentials at rest. Required outside local |
| `AUTUNE_LOG_LEVEL` | `INFO` | |
| `AUTUNE_RETENTION_DAYS` | `90` | Default analysis retention |

### Integrations

| Variable | Used by |
| --- | --- |
| `AUTUNE_SLACK_BOT_TOKEN`, `AUTUNE_SLACK_SIGNING_SECRET` | `apps/bot`, all modules that notify |
| `AUTUNE_SLACK_APP_TOKEN` | `apps/bot` socket mode, local development only |
| `AUTUNE_LLM_API_KEY` | Any module using an LLM |

### Module-specific

| Variable | Module | Meaning |
| --- | --- | --- |
| `AUTUNE_AUDIO_WHISPER_MODEL` | A | e.g. `large-v3` |
| `AUTUNE_AUDIO_DEVICE` | A | `cuda` or `cpu` |
| `AUTUNE_AUDIO_TEMP_DIR` | A | Where the recording lives during processing, and only then |
| `AUTUNE_GAP_RISK_THRESHOLD` | C | Default `0.7` |
| `AUTUNE_CONTEXT_RERANK_TOP_K` | D | Default `10` |

Notion, Jira and Calendar credentials are **not** environment variables. Each
team configures its own on screen S28 and they are stored encrypted in
`team_integrations` — read them with `autune_core.load_integration`, never from
settings. See `../architecture/data-model.md`.

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
| Import error for `autune_core` | `uv sync` was run without `--all-packages`. The root is a virtual workspace, so a plain sync installs no members. Re-run `uv sync --all-packages` |
| `pytest` fails collecting another module's tests | The environment was built with `uv sync --package <yours>`, which installs only your module. Use `uv sync --all-packages`, or run `uv run pytest modules/<yours>` |
| Celery task never runs | Worker is not listening on that queue. Check `-Q` |
| import-linter fails | You imported another module. Fix the import, not the config |
| Whisper is very slow | Running on CPU. Set `AUTUNE_AUDIO_DEVICE=cuda` or use a smaller model locally |
| Generated TS types are stale in CI | Run `pnpm run gen:contracts` and commit the output |
