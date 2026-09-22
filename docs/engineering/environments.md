# Environments and Local Setup

## Prerequisites

- Python 3.12 (`.python-version`)
- Node 22 (`.nvmrc`)
- uv, pnpm 9
- Docker and Docker Compose
- FFmpeg — `brew install ffmpeg` (macOS) or `apt install ffmpeg`. Module A needs
  it to decode uploads; see "FFmpeg" below
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

uv run uvicorn autune_api.main:app --reload          # API   :8000 -- one worker, see below
uv run celery -A autune_worker.celery_app worker -Q default,cpu_heavy,gpu -l info
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
| `AUTUNE_CORS_ALLOWED_ORIGINS` | `` | Comma-separated origins `apps/api` allows via CORS. Empty (default) means no CORS headers at all. Set to `http://localhost:3000` for local dev when running `apps/web`'s dev server against `apps/api`'s — a browser blocks the response otherwise, since `:3000` and `:8000` are different origins. Outside `local`, every origin must be an explicit `https://` URL — `*` and plain `http://` are refused at startup |

### Integrations

| Variable | Used by |
| --- | --- |
| `AUTUNE_SLACK_BOT_TOKEN`, `AUTUNE_SLACK_SIGNING_SECRET` | `apps/bot`, all modules that notify |
| `AUTUNE_SLACK_APP_TOKEN` | `apps/bot` socket mode, local development only |

### Module-specific

| Variable | Module | Meaning |
| --- | --- | --- |
| `AUTUNE_AUDIO_WHISPER_MODEL` | A | e.g. `large-v3` |
| `AUTUNE_AUDIO_DEVICE` | A | `cuda` or `cpu` |
| `AUTUNE_AUDIO_TEMP_DIR` | A | Where the recording lives during processing, and only then |
| `AUTUNE_AUDIO_LIVE_HELLO_TIMEOUT_S` | A | How long a live socket may wait for `hello` (5) |
| `AUTUNE_AUDIO_LIVE_MAX_SESSION_S` | A | The longest live session, 3 h; the recording is in the browser |
| `AUTUNE_AUDIO_LIVE_MAX_FRAME_BYTES` | A | One second of PCM16; a bigger frame is refused, not buffered |
| `AUTUNE_AUDIO_LIVE_FRAME_MS` | A | What the browser is asked to send (200) |
| `AUTUNE_AUDIO_LIVE_WHISPER_MODEL` | A | The live channel's own model, default `large-v3-turbo`; the stored path keeps `AUTUNE_AUDIO_WHISPER_MODEL` |
| `AUTUNE_AUDIO_LIVE_CPU_THREADS` | A | CTranslate2 threads for the live model; 0 = CTranslate2 default, set to the machine's performance-core count |
| `AUTUNE_AUDIO_LIVE_BEAM_SIZE` | A | Beam width on the live path (5) |
| `AUTUNE_AUDIO_LIVE_MIN_SILENCE_MS` | A | Silence that ends a live utterance (1000). Longer keeps sentences whole and adds that much lag to every row |
| `AUTUNE_AUDIO_LIVE_MIN_CONFIDENCE` | A | A live row below this mean word probability is not sent (0.35); the stored transcript is the final form |
| `AUTUNE_AUDIO_LIVE_TRANSCRIBER_IMPL` | A | `auto` (default) · `faster_whisper` · `mlx`. `mlx` is the live path on Apple silicon's GPU and needs `uv sync --all-packages --extra mlx`; `faster_whisper` follows `AUTUNE_AUDIO_DEVICE`, so an NVIDIA machine sets that to `cuda`. `auto` picks `mlx` where it can run |
| (uvicorn `--workers`) | A | **Leave at 1.** The live channel's one-session-per-meeting claim (`live/registry.py`) is per process: a second worker lets a second session onto the same meeting, and accepts an upload the other worker's open socket should have refused (409) |
| `AUTUNE_AUDIO_LIVE_MLX_MODEL` | A | The mlx-whisper weights, a Hugging Face repo. Default `mlx-community/whisper-large-v3-turbo` |
| `AUTUNE_AUDIO_ORPHAN_AFTER_HOURS` | A | A job still `queued`/`running` after this long has no worker; the sweep fails it and deletes its file. Default `6` |
| `AUTUNE_AUDIO_HF_TOKEN` | A | Hugging Face token for the gated pyannote models |
| `AUTUNE_AUDIO_DIARIZATION_NUM_SPEAKERS` | A | Exactly how many people spoke, when the room knows (#325). Unset by default: pyannote clusters freely, and a wrong number is worse than none. Deployment-wide for now; the per-meeting field comes with S10 |
| `AUTUNE_AUDIO_DIARIZATION_MIN_SPEAKERS` / `…_MAX_SPEAKERS` | A | Bounds instead of an exact count. Ignored when `…_NUM_SPEAKERS` is set |
| `NEXT_PUBLIC_AUTUNE_DEV_TOKEN` | A (web) | A bearer token for the browser, local only — see "A token for the browser" below |
| `AUTUNE_AUDIO_DIARIZATION_MODEL` | A | Default `pyannote/speaker-diarization-3.1` |
| `AUTUNE_EXTRACTION_CLASSIFIER_IMPL` | B | `local` · `hosted` · `fake`. Default `local`. **No `external`** — see below |
| `AUTUNE_EXTRACTION_CLASSIFIER_CHECKPOINT` | B | Pinned model, recorded with every classification. Never a floating tag. **Blank by default** — no trained checkpoint is published yet, and `local` / `hosted` refuse to start without one |
| `AUTUNE_EXTRACTION_CLASSIFIER_ENDPOINT` | B | Our own inference server. Required when `CLASSIFIER_IMPL=hosted` |
| `AUTUNE_EXTRACTION_CLASSIFIER_DEVICE` | B | `cpu` · `cuda`. Default `cpu`. Mirrors `AUTUNE_AUDIO_DEVICE` |
| `AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE` | B | Below this, an item is a candidate rather than asserted. **Blank by default** — the number comes from the evaluation set (#10), and blank means nothing is a candidate |
| `AUTUNE_GAP_RISK_THRESHOLD` | C | Default `0.7`. At or above is `high`, the only severity surfaced |
| `AUTUNE_GAP_MEDIUM_THRESHOLD` | C | Default `0.5`. Down to here is `medium`, below it `low` |
| `AUTUNE_GAP_DEFAULT_TEMPLATE` | C | Default `general`. Which domain template a meeting nobody chose one for is held to |
| `AUTUNE_GAP_PARTIAL_CENTRALITY` | C | Default `0.4`. A matched topic below this makes the item *partial* rather than covered |
| `AUTUNE_GAP_PARTIAL_DAMPING` | C | Default `0.7`. What a partial finding's risk score is multiplied by |
| `AUTUNE_GAP_WEIGHT_TEMPLATE` · `_COVERAGE` · `_PARTICIPATION` | C | Defaults `0.4` · `0.4` · `0.2`. The three risk inputs, relative; renormalised over whichever could be measured |
| `AUTUNE_GAP_NER_IMPL` | C | `spacy` (default) · `fake`. **No `external`** — see below |
| `AUTUNE_GAP_NER_MODEL` | C | Default `ko_core_news_lg`. The pipeline **name**; the version comes from the pinned wheel and is recorded per row |
| `AUTUNE_CONTEXT_EMBEDDER_IMPL` | D | `kure_v1_http` (default), `kure_v1_local`, `fake` |
| `AUTUNE_CONTEXT_RERANKER_IMPL` | D | `bge_reranker_v2_m3_ko_http` (default), `..._local`, `fake` |
| `AUTUNE_CONTEXT_NLI_IMPL` | D | `klue_kornli_http` (default), `klue_kornli_local`, `fake` |
| `AUTUNE_CONTEXT_EMBEDDING_DIM` | D | Must match the model behind `EMBEDDER_IMPL`. Default `1024` (KURE-v1) |
| `AUTUNE_CONTEXT_EMBEDDER_ENDPOINT` | D | Self-hosted KURE-v1 inference server |
| `AUTUNE_CONTEXT_RERANKER_ENDPOINT` | D | Self-hosted reranker inference server |
| `AUTUNE_CONTEXT_NLI_ENDPOINT` | D | Self-hosted NLI inference server |
| `AUTUNE_CONTEXT_EMBEDDER_TIMEOUT_S` | D | HTTP timeout, seconds. Default `10.0` |
| `AUTUNE_CONTEXT_RERANKER_TIMEOUT_S` | D | HTTP timeout, seconds. Default `10.0` |
| `AUTUNE_CONTEXT_NLI_TIMEOUT_S` | D | HTTP timeout, seconds. Default `10.0` |
| `AUTUNE_CONTEXT_EMBEDDER_LOCAL_MODEL` | D | Only for `kure_v1_local`. Default `nlpai-lab/KURE-v1` |
| `AUTUNE_CONTEXT_RERANKER_LOCAL_MODEL` | D | Only for `bge_reranker_v2_m3_ko_local`. Default `dragonkue/bge-reranker-v2-m3-ko` |
| `AUTUNE_CONTEXT_NLI_LOCAL_MODEL` | D | Only for `klue_kornli_local`. Path or hub id of the in-house checkpoint |
| `AUTUNE_CONTEXT_TOPIC_WINDOW` | D | TextTiling block size, in utterances. Default `3` |
| `AUTUNE_CONTEXT_TOPIC_MIN_SEGMENT` | D | Shortest topic segment. Default `3` |
| `AUTUNE_CONTEXT_TOPIC_DEPTH_THRESHOLD` | D | Min TextTiling depth for a boundary. Default `0.1` |
| `AUTUNE_CONTEXT_RETRIEVE_TOP_K` | D | Hybrid retrieval breadth. Default `50` |
| `AUTUNE_CONTEXT_RERANK_TOP_K` | D | Kept after re-ranking. Default `10` |
| `AUTUNE_CONTEXT_RRF_K` | D | Reciprocal-rank-fusion constant. Default `60` |
| `AUTUNE_CONTEXT_LINK_CONFIDENCE_THRESHOLD` | D | Assert vs. ask. Default `0.6`, tuned in eval |
| `AUTUNE_CONTEXT_LINEAGE_MATCH_THRESHOLD` | D | Decision-to-thread match cutoff (cosine). Default `0.6`, tuned in eval |
| `AUTUNE_CONTEXT_PUBLISH_TIMEOUT_S` | D | Wait for B before publishing. Default `600` |
| `AUTUNE_CONTEXT_MAX_TOPIC_LINK_NOTICES` | D | Individual topic-link Slack messages per meeting before the rest roll up into one notice. Default `3` |
| `AUTUNE_CONTEXT_WARM_MODELS_ON_WORKER_INIT` | D | `true` only on workers consuming `cpu_heavy`. Default `false` |
| `AUTUNE_INTELLIGENCE_AGGREGATE_TIMEOUT_SECONDS` | E | Wait for B/C/D before aggregating without the rest. Default `600` |
| `AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL` | E | `local` (default) · `fake`. **No `external`, no `hosted`** — see below |
| `AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_BACKBONE` | E | Sentence-embedding backbone SetFit fits its few-shot head onto. Default `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| `AUTUNE_INTELLIGENCE_WARM_MODELS_ON_WORKER_INIT` | E | `true` only on workers consuming gap-classification tasks. Default `false` |

Notion, Jira and Calendar credentials are **not** environment variables. Each
team configures its own on screen S28 and they are stored encrypted in
`team_integrations` — read them with `autune_core.load_integration`, never from
settings. See `../architecture/data-model.md`.

Every new variable goes into `.env.example` with a comment and into this table.
A variable that exists only in someone's local `.env` will break the next
person's setup.

### The classifier has no external option

`AUTUNE_EXTRACTION_CLASSIFIER_IMPL` accepts `local`, `hosted` and `fake`, and
nothing else. Module B classifies every utterance in a meeting, so an external
implementation would mean sending the whole transcript to somebody else's model —
which section 6 of `../architecture/privacy.md` makes a design conversation rather
than a value you can set.

`hosted` points at an inference server we run. It still goes through
`autune_integrations.HttpClient` so the outbound guard reads the request body:
the endpoint being ours is exactly the reasoning that leaves a guard unrun.

That guard caps a request at 4,000 characters, which one meeting is far over, so
`hosted` splits its batches to fit rather than the cap being widened for our own
host. A meeting of 3,000 utterances becomes roughly 28 requests.

`local` needs weights and a library, and the library is an optional extra:

```bash
uv sync --package autune-extraction --extra local-models
```

It is not an ordinary dependency because `apps/api` serves a health check and
must not load a deep-learning stack to do it, and a worker on `hosted` never
touches it. Without the extra the classifier raises a `RuntimeError` naming this
command — the default implementation failing with `No module named
'transformers'` tells the reader nothing about the extra existing.

### A GPU is not picked up by being there

`AUTUNE_EXTRACTION_CLASSIFIER_DEVICE` defaults to `cpu` and is never inferred
from the machine. A worker that quietly takes whichever hardware it landed on
has a throughput that changes when it is rescheduled, and a latency measured on
one scheduling says nothing about the other.

Setting it to `cuda` needs a CUDA build of torch, which the extra does **not**
install. `torch>=2.5` from PyPI resolves to a CPU-only wheel on Windows and
Linux alike; a version ending in `+cpu` has no CUDA support whatever the machine
reports. Install the CUDA build from PyTorch's own index:

```bash
uv pip install torch --index-url https://download.pytorch.org/whl/cu121
```

Pinning that in the extra would make every checkout download a multi-gigabyte
CUDA wheel, including the ones that only ever run `fake` — so it stays a manual
step, and the classifier raises rather than falling back when the two disagree.
Falling back would turn a missing GPU into a silent thirty-fold slowdown, which
reads as the model being slow rather than the box being wrong.

This module classifies every utterance of every meeting, so it is the heaviest
inference in the product — heavier than module A, which runs its model once per
recording.

### The entity extractor has no external option

`AUTUNE_GAP_NER_IMPL` accepts `spacy` and `fake`, and nothing else. Module C
extracts entities from **every** utterance in a meeting, so an external
implementation would mean sending the whole transcript to somebody else's
model — which section 6 of `../architecture/privacy.md` makes a design
conversation rather than a value you can set. The same reasoning module B
applied to its classifier.

`spacy` needs a library and a model, and both come from the optional extra:

```bash
uv sync --package autune-gap --extra local-models
```

The model is in the extra as a wheel URL rather than left to
`python -m spacy download`, which resolves to whichever version is current on
the day somebody runs it. `uv.lock` pins the wheel, so two checkouts extract
with the same model — and `AUTUNE_GAP_NER_MODEL` names the pipeline while the
version travels with the rows it produced.

Without the extra the extractor raises a `RuntimeError` naming the command —
the default implementation failing with `No module named 'spacy'` tells the
reader nothing about the extra existing.

### The gap classifier has no external or hosted option

`AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL` accepts `local` and `fake`, and
nothing else. It classifies gaps across a team's whole meeting history —
exactly the aggregation section 3 of `../architecture/privacy.md` asks module
E to be careful with — so an external implementation is a design conversation,
not a config value. That much is the same reasoning modules B and C give for
ruling out `external` on their own model-facing settings; it says nothing
about `hosted`, which B does have (`AUTUNE_EXTRACTION_CLASSIFIER_IMPL` above).

E has no `hosted` for an unrelated reason: unlike B's classifier or C's NER
model, there is no separate checkpoint to pin and no inference server to point
at. `local` is SetFit, which fits a small classification head on top of a
general sentence-embedding backbone (`AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_BACKBONE`)
from a handful of labeled examples checked into
`autune_intelligence.pipeline.classifier`, refit once per process on first use.
There is nothing to host — "the checkpoint" is the backbone name plus that
seed set, both already in the repo. The examples are a seed set nobody has
evaluated against real `GapReport` traffic yet — see that module's docstring
before trusting the distribution it produces.

It needs a library and a backbone download, both from the optional extra:

```bash
uv sync --package autune-intelligence --extra local-models
```

Without the extra the classifier raises a `RuntimeError` naming this command,
the same shape B's and C's local implementations use.

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

Pin versions explicitly in code (`../engineering/conventions.md`).

### Pyannote and its three gated repositories

Diarization needs a Hugging Face token in `AUTUNE_AUDIO_HF_TOKEN` and the licence
accepted on **three** repositories. The pipeline loads the other two itself, so
accepting only the first fails partway through, with an error naming a model you
never asked for:

| Repository | Why |
| --- | --- |
| `pyannote/speaker-diarization-3.1` | The pipeline you ask for |
| `pyannote/segmentation-3.0` | Speech segmentation, loaded by the pipeline |
| `pyannote/speaker-diarization-community-1` | PLDA for speaker comparison. **New in pyannote.audio 4.x** — 3.x tutorials do not mention it |

A fine-grained token needs "Read access to contents of all public gated repos
you can access"; a plain Read token already has it.

### pyannote.audio 4.x differs from the tutorials

Most material online is 3.x. Two things changed:

```python
# 3.x, and every tutorial
pipeline = Pipeline.from_pretrained(model, use_auth_token=token)
for turn, _, speaker in pipeline(path).itertracks(yield_label=True):
    ...

# 4.x, what we run
pipeline = Pipeline.from_pretrained(model, token=token)
output = pipeline({"waveform": waveform, "sample_rate": sr})
for turn, _, speaker in output.speaker_diarization.itertracks(yield_label=True):
    ...
```

`use_auth_token` no longer exists, and the result is a `DiarizeOutput` rather
than an `Annotation`. It carries `speaker_diarization`,
`exclusive_speaker_diarization`, and — useful for speaker identification —
`speaker_embeddings`, one 256-dimension vector per speaker. Module A does not
need a separate embedding model.

### FFmpeg

pyannote 4.x decodes audio through `torchcodec`, which links against FFmpeg's
shared libraries. Without them, passing a **file path** to the pipeline fails
with `Library not loaded: @rpath/libavutil.*`. Passing a waveform already in
memory works without FFmpeg, but uploads arrive as mp3, wav and m4a, so decoding
them needs it either way.

## A token for the browser, until there is a sign-in

Every route that matters takes `CurrentUser`, and screen S01 does not exist yet
(#156, #189). On a developer's machine, module A's dev router issues a token:

```bash
curl -s -X POST localhost:8000/api/audio/dev/token \
  -H 'content-type: application/json' \
  -d '{"email": "you@example.com", "team_name": "Dev Team"}'
# → {"token": "...", "user_id": "user_…", "team_id": "team_…"}
```

It creates the user, the team and the membership if they do not exist, and
returns the same ones on every later call for that email. The route is under
`/dev`, so it is mounted only when `AUTUNE_ENV=local`; there is no such route
anywhere else.

Give the token to the browser one of two ways:

- `apps/web/.env.local`: `NEXT_PUBLIC_AUTUNE_DEV_TOKEN=<token>` — inlined at
  build time, so restart `next dev` after changing it.
- In the browser console: `localStorage.setItem("autune.token", "<token>")` —
  takes effect on the next request, and lets you switch users without a
  rebuild. This wins over the environment variable when both are set.

Tokens last seven days (`autune_core.auth.DEFAULT_TTL`). The `team_id` in the
response is what `POST /api/audio/meetings` needs.

## Local privacy hygiene

`AUTUNE_AUDIO_TEMP_DIR` holds real audio while a task runs. It is gitignored and
cleared at the end of every task. Do not point it at a synced folder, and do not
keep test recordings of real meetings on disk. See
`../architecture/privacy.md`.

While an upload request is in flight there is a second, short-lived copy of the
recording in the OS temporary directory (`tempfile.gettempdir()`), written by
Starlette's multipart parser before module A's code runs. It is deleted when
the request closes. `AUTUNE_AUDIO_TEMP_DIR` is the copy this module owns and
checks; the other one is the web framework's, and the same "not a synced
folder" rule applies to `TMPDIR` on a developer machine.

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
