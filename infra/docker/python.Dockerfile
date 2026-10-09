# syntax=docker/dockerfile:1
# One image for apps/api, apps/worker and the alembic migrations. They share the
# uv workspace, so one `uv sync` builds all three; compose picks the command.
FROM python:3.12-slim-bookworm

# Module A decodes every upload with ffmpeg.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a /uv /usr/local/bin/uv

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
# Dependencies before source. This layer is torch, transformers and the CUDA
# libraries -- several GB -- and Docker rebuilds it whenever a file copied
# above it changes. With the source copied first, one changed line of Python
# rebuilt and exported all of it (#984: 961 s of `uv sync`, and the engine went
# away exporting the layer, twice in a day). `--frozen` installs what `uv.lock`
# says and reads no member's `pyproject.toml`, so these three files are all the
# layer depends on; `--no-install-workspace` leaves our own packages to the
# second sync below.
COPY pyproject.toml uv.lock .python-version ./

# `--all-packages`: the modules are workspace members. `local-models` puts in
# what C, D and E run in process (spaCy, SetFit, KURE-v1); without it they
# raise on first use and the API still answers 200 -- see scripts/up.sh.
# `cuda` is module A's: CUDA 12's cuBLAS, which faster-whisper needs on a GPU
# and torch's CUDA 13 does not provide (modules/audio/pyproject.toml).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages --extra local-models --extra cuda \
        --no-install-workspace

# Named paths, never `COPY . .`: on a teammate's machine the repository root
# also holds AI Hub corpora and personal files git ignores (#517 review).
# apps/bot is here only because it is a workspace member uv sync expects.
COPY packages packages
COPY modules modules
COPY agent agent
COPY apps/api apps/api
COPY apps/worker apps/worker
COPY apps/bot apps/bot
COPY infra/alembic.ini infra/alembic.ini
COPY infra/alembic infra/alembic

# The same sync, now with the workspace members: everything they need is in
# the layer above, so this one holds our twelve packages and nothing else.
# Keep the flags of the two the same, or this one starts adding or removing
# dependencies.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages --extra local-models --extra cuda

# CTranslate2 dlopens libcublas.so.12 by name, and a pip-installed CUDA library
# is not on the loader's path. Harmless where there is no GPU: nothing loads it.
ENV LD_LIBRARY_PATH=/opt/venv/lib/python3.12/site-packages/nvidia/cublas/lib

USER autune
