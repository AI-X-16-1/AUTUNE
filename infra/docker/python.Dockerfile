# syntax=docker/dockerfile:1
# One image for apps/api, apps/worker and the alembic migrations. They share the
# uv workspace, so one `uv sync` builds all three; compose picks the command.
FROM python:3.12-slim-bookworm

# Module A decodes every upload with ffmpeg.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models/huggingface \
    AUTUNE_AUDIO_MODEL_CACHE=/models/whisper

RUN useradd --create-home --uid 1000 autune \
 && mkdir -p /models /tmp/autune-audio \
 && chown autune:autune /models /tmp/autune-audio

WORKDIR /app
COPY . .

# `--all-packages`: the modules are workspace members. `local-models` puts in
# what C, D and E run in process (spaCy, SetFit, KURE-v1); without it they
# raise on first use and the API still answers 200 -- see scripts/up.sh.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages --extra local-models

USER autune
