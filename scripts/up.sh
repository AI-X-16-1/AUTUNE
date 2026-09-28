#!/usr/bin/env bash
# Start Autune locally: API, worker, web. One command, from a fresh clone.
#
# This is `docs/engineering/demo-runbook.md` sections 1 and 2 with the parts
# nobody should have to remember baked in -- which module implementations to
# fake, and why. The runbook stays the explanation; this is the thing you run.
#
#   ./scripts/up.sh                 # start everything
#   ./scripts/up.sh --real-models   # no fakes, for a machine that has them
#   ./scripts/down.sh               # stop everything
#
# Requires: docker, ffmpeg, uv, pnpm, and `.env` with AUTUNE_AUDIO_HF_TOKEN.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT=$(pwd)
LOGS="$ROOT/.logs"
mkdir -p "$LOGS"

REAL_MODELS=0
[ "${1:-}" = "--real-models" ] && REAL_MODELS=1

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# --- what has to be here before anything starts ------------------------------
[ -f .env ] || die ".env is missing. Copy .env.example and fill AUTUNE_AUDIO_HF_TOKEN
  (pyannote is gated -- accept the licence on speaker-diarization-3.1,
   segmentation-3.0 and speaker-diarization-community-1, or diarization fails
   partway through loading and names a model you never asked for)."
command -v ffmpeg >/dev/null || die "ffmpeg is missing. Module A decodes with it; no ffmpeg, no upload."
command -v uv >/dev/null || die "uv is missing. See docs/engineering/environments.md."
command -v docker >/dev/null || die "docker is missing. PostgreSQL and Redis run in it."

grep -q '^AUTUNE_AUDIO_HF_TOKEN=.' .env \
  || say "WARNING: AUTUNE_AUDIO_HF_TOKEN looks empty in .env. Transcription will
work and diarization will fail -- every speaker comes back as one."

set -a
# shellcheck disable=SC1091
source .env
set +a
export AUTUNE_ENV=${AUTUNE_ENV:-local}

# The page sends `Authorization`, so the browser preflights, and the API
# answers a preflight only for a listed origin (#241). Without this the page
# says "Failed to fetch" while curl works fine.
export AUTUNE_CORS_ALLOWED_ORIGINS=${AUTUNE_CORS_ALLOWED_ORIGINS:-http://localhost:3000}

# --- which implementations to run --------------------------------------------
# Defaults are what a laptop can actually run today. Each one is a line in the
# runbook's troubleshooting table; the failure it prevents is in the comment,
# because all of these fail by returning *nothing* while every endpoint still
# answers 200 -- which is how a broken stack looks healthy.
if [ "$REAL_MODELS" = 0 ]; then
  # B's trained checkpoints are not published (#112).
  export AUTUNE_EXTRACTION_CLASSIFIER_IMPL=${AUTUNE_EXTRACTION_CLASSIFIER_IMPL:-fake}
  # B's NLI step (#12) defaults to `local` and refuses to run without a
  # checkpoint; the one #172 settled on is a private HF repo.
  export AUTUNE_EXTRACTION_NLI_IMPL=${AUTUNE_EXTRACTION_NLI_IMPL:-fake}
  # D's embedder/reranker/NLI want an inference server that is not running.
  export AUTUNE_CONTEXT_EMBEDDER_IMPL=${AUTUNE_CONTEXT_EMBEDDER_IMPL:-fake}
  export AUTUNE_CONTEXT_RERANKER_IMPL=${AUTUNE_CONTEXT_RERANKER_IMPL:-fake}
  export AUTUNE_CONTEXT_NLI_IMPL=${AUTUNE_CONTEXT_NLI_IMPL:-fake}
fi
# C and E run their real models from the `local-models` extra, installed below.
export AUTUNE_GAP_NER_IMPL=${AUTUNE_GAP_NER_IMPL:-spacy}
export AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL=${AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL:-local}

# --- infrastructure ----------------------------------------------------------
say "docker: postgres + redis"
docker compose -f infra/docker-compose.yml up -d

say "python: workspace + the extras C and E need"
# `--all-packages`, not plain `uv sync`: the modules are workspace members.
# `local-models` is what puts spaCy in for C and SetFit for E -- without it
# both raise on first use and return nothing while answering 200.
uv sync --all-packages --extra local-models

say "javascript"
pnpm install --silent

say "database: six alembic branches"
# `heads`, plural: each module owns an independent branch (invariant 7).
uv run alembic -c infra/alembic.ini upgrade heads

# --- processes ---------------------------------------------------------------
"$ROOT/scripts/down.sh" --quiet 2>/dev/null || true

say "api  :8000"
nohup uv run uvicorn autune_api.main:app --port 8000 > "$LOGS/api.log" 2>&1 &
echo $! > "$LOGS/api.pid"

say "worker"
# --pool=solo: module E's classifier aborts on Metal under prefork or threads
# (#329), and the Windows prefork pool kills its own children (README).
nohup uv run celery -A autune_worker.celery_app worker \
  -Q default,cpu_heavy,gpu -l info --pool=solo > "$LOGS/worker.log" 2>&1 &
echo $! > "$LOGS/worker.pid"

say "web  :3000"
nohup pnpm --filter @autune/web dev > "$LOGS/web.log" 2>&1 &
echo $! > "$LOGS/web.pid"

for _ in $(seq 60); do
  curl -sf http://localhost:8000/health >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://localhost:8000/health >/dev/null 2>&1 \
  || die "the API did not come up. $LOGS/api.log"

printf '\n\033[1mup\033[0m\n'
printf '  api     http://localhost:8000/health  %s\n' "$(curl -s http://localhost:8000/health)"
printf '  web     http://localhost:3000\n'
printf '  logs    %s/{api,worker,web}.log\n' "$LOGS"
printf '\nFirst upload: open http://localhost:3000, make a meeting, upload a recording.\n'
printf 'The first run downloads Whisper large-v3 (several GB) -- watch worker.log.\n'
printf 'Stop with ./scripts/down.sh\n'
