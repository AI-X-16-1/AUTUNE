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

### The whole pipeline on a laptop, without model servers

The defaults of C, D and E expect what a laptop does not have: C's entity
extractor needs the `local-models` extra, D's three models are HTTP services
(`autune-embed.internal` and friends), and E's gap classifier needs the
`local-models` extra too. With the defaults, a meeting processed locally stops
at C and D with a `RuntimeError`; with those two on fakes, E's aggregate stops
at its classifier the same way. Either way `autune.intelligence.completed` is
never published, and nothing after it — the agents included — is woken.

To follow one meeting end to end — to see what the agents do after it, for
instance — put those on their test fakes in the shell that runs the worker:

```bash
export AUTUNE_GAP_NER_IMPL=fake
export AUTUNE_CONTEXT_EMBEDDER_IMPL=fake
export AUTUNE_CONTEXT_RERANKER_IMPL=fake
export AUTUNE_CONTEXT_NLI_IMPL=fake
export AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL=fake
```

The fakes are deterministic and read nothing: what C, D and E report for that
meeting is placeholder, and only the plumbing is real. To see a module's real
output instead, install its extra or start its service and leave its line out.
Found in the 2026-10-02 agent check, where every subagent ran only after all
five were set.

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
| `AUTUNE_ENV` | `local` | `local`, `staging`, `production`. **Unset means `production`** (#408), so a deployment that forgets it gets production's startup checks instead of running as a dev box. `.env.example`, CI and `scripts/up.sh` set `local` |
| `AUTUNE_DATABASE_URL` | `postgresql+psycopg://autune:autune@localhost:5432/autune` | |
| `AUTUNE_REDIS_URL` | `redis://localhost:6379/0` | |
| `AUTUNE_SECRET_KEY` | | JWT signing. Never commit |
| `AUTUNE_ENCRYPTION_KEY` | | Encrypts team integration credentials at rest. Required outside local |
| `AUTUNE_LOG_LEVEL` | `INFO` | |
| `AUTUNE_RETENTION_DAYS` | `90` | Default analysis retention |
| `AUTUNE_CORS_ALLOWED_ORIGINS` | `` | Comma-separated origins `apps/api` allows via CORS. Empty (default) means no CORS headers at all. Set to `http://localhost:3000` for local dev when running `apps/web`'s dev server against `apps/api`'s — a browser blocks the response otherwise, since `:3000` and `:8000` are different origins. Outside `local`, every origin must be an explicit `https://` URL — `*` and plain `http://` are refused at startup |
| `AUTUNE_WEB_BASE_URL` | `http://localhost:3000` | Where the OAuth callback sends the browser back to |

### Sign-in

| Variable | Example | Notes |
| --- | --- | --- |
| `AUTUNE_GOOGLE_CLIENT_ID` | | Google Cloud OAuth client (W2). If any of these three is blank, Google sign-in is off: `/api/auth/providers` reports `google: false` and S01 disables the button |
| `AUTUNE_GOOGLE_CLIENT_SECRET` | | Never commit |
| `AUTUNE_GOOGLE_REDIRECT_URI` | `http://localhost:3000/api/auth/google/callback` | The **web** origin, not the API — the browser reaches `/api/*` through the Next proxy, so the callback must land there too. Must match a redirect URI registered in the Google Cloud console exactly, per environment |
| `API_PROXY_TARGET` | `http://localhost:8000` | Web-only (read by `apps/web/next.config.ts`), where `/api/*` is proxied. Set per environment; not an `autune_core` setting |

Google *sign-in* is identity only (`openid email profile`). A person's calendar
is a separate consent, given after signing in and described next. Nothing reads
`AUTUNE_GOOGLE_CALENDAR_CREDENTIALS`, which this page used to name here.

### A person's own Google grant

| Variable | Example | Notes |
| --- | --- | --- |
| `AUTUNE_GOOGLE_INTEGRATION_CLIENT_ID` | | A second Google Cloud OAuth client, for what a person connects after signing in — their own calendar today (#435), mail when it exists. Optional: blank, the sign-in client does both |
| `AUTUNE_GOOGLE_INTEGRATION_CLIENT_SECRET` | | Never commit. Set with the id or not at all — one without the other is refused at startup. With the pair set, `AUTUNE_GOOGLE_REDIRECT_URI` is required too, and its absence is refused at startup |

Sign-in keeps `AUTUNE_GOOGLE_CLIENT_ID`. The two are separate so that an
identity-only client and one that asks for somebody's calendar can be reviewed
and rotated apart. `Settings.google_integration_credentials` is the one place
that chooses: the integration pair when it is set, the sign-in pair otherwise.
The connect route in `autune_core` and module B's calendar refresh both read
it, so a grant is always refreshed with the client that issued it.

- **No second redirect URI.** One callback (`/api/auth/google/callback`)
  finishes sign-in and the calendar connect, so `AUTUNE_GOOGLE_REDIRECT_URI`
  must be registered on the integration client as well as on the sign-in one.
  The integration pair without that variable is refused at startup: the
  connect would start and then have no callback to finish on.
- **Setting these on a running deployment strands its connected calendars.**
  Google binds a refresh token to the client that issued it. A grant issued
  to the sign-in client is refused at its next refresh once the integration
  client is in use, and the person is asked to connect again. Nothing migrates
  a grant from one client to the other.
- **A grant records the client that issued it.** A connect stores the
  issuing `client_id` beside the grant (public by nature, not a credential).
  When it differs from the client the deployment refreshes with now,
  `GET /api/auth/google/calendar` reports `needs_reconnect: true`, the
  calendar card says the connection is broken, and module B skips the
  refresh instead of sending one Google would refuse. A grant from before
  the client was recorded says nothing either way and is tried as before —
  which includes every calendar connected before #711, so the first switch
  to an integration client is still found out by a refused refresh. With no
  Google client configured at all, nothing is reported as needing a
  reconnect: there is nothing to reconnect to.

**Two cookies, two jobs.** `autune_session` is the signed session (7 days,
`HttpOnly`, `SameSite=Lax`, `Secure` outside local). `autune_oauth_state` lives
only for the 600 seconds of one sign-in, is scoped to the callback's own path,
and holds the OAuth `state`: the callback refuses a request whose cookie does
not match the `state` in the query, so a callback URL opened in somebody else's
browser cannot sign them in as whoever started it. Redis proves a state was
issued; the cookie is what proves to whom.
`packages/core/src/autune_core/auth_router.py` has the reasoning.

**Signing out ends the person's sessions on the server, on every device.**
`POST /logout` writes the moment on the person's row
(`users.sessions_valid_from`) and clears the cookie; from then on
`current_user` refuses every token of theirs issued up to it — the browser
that signed out, another browser, a developer token, a copy that leaked. The
next sign-in issues a token after that moment, which is good. It is one value
per person, not a session per device: signing out in one place signs out
everywhere, and there is no list of sessions to look at. Deploying this signed
nobody out — a person who has never signed out has no moment to compare
with. To end **everybody's** sessions at once, rotate `AUTUNE_SECRET_KEY`.

Every door that takes a session token asks the same function
(`autune_core.auth.user_for_token`): the HTTP routes through `current_user`,
and module A's live WebSocket directly, because its handler cannot use a
dependency. The check is made when a request or a connection arrives — **a
live socket opened before the sign-out stays open until it closes**; it is
not cut off mid-recording.

### Web (`apps/web`)

`NEXT_PUBLIC_` variables are inlined into the browser bundle at build time, so
nothing secret goes here.

| Variable | Example | Notes |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Where the browser reaches `apps/api` |
| `NEXT_PUBLIC_AUTUNE_DEV_TOKEN` | | Bearer token for every call, until sign-in (S01) exists. Build-time fallback for the value below |

The web app proxies `/api/*` to the API (`next.config.ts`) so the browser sees
one origin and the `autune_session` cookie stays first-party. `NEXT_PUBLIC_API_URL`
overrides the client base only if you deliberately want cross-origin calls.

**Signing in.** Routes that take `CurrentUser` refuse a request with neither a
session cookie nor a bearer token. Google sign-in (S01) sets the cookie. A tab
without a session falls back to a developer token: `@/shared/api/client`
attaches one to every call it makes, preferring `localStorage["autune.token"]`
over `NEXT_PUBLIC_AUTUNE_DEV_TOKEN`. With neither, the app sends you to
`/login`.

Where that token comes from, and the two ways to give it to the browser:
"A developer token for the browser" below.

### Integrations

| Variable | Used by |
| --- | --- |
| `AUTUNE_SLACK_SIGNING_SECRET` | `apps/bot`, and `apps/api` when set: it mounts `POST /api/slack/events`, Slack's **Request URL** for Interactivity (#585) — set it in the Slack app to `https://<web origin>/api/slack/events`. Bolt refuses a request whose signature does not match. Each request is answered with the bot token its workspace's team stored (`team_integrations`), so a deployment needs no global token |
| `AUTUNE_SLACK_BOT_TOKEN` | `apps/bot` socket mode only (one workspace, local development). Leave empty in a deployment: a team that connected with "Add to Slack" is answered with its own stored token |
| `AUTUNE_SLACK_BUTTONS` | B's confirmation DM carries the three answers as buttons (default `false`). Turn on only where Slack can reach `/api/slack/events` (public HTTPS) or a socket-mode bot runs; otherwise a button does nothing, and the DM's link to Autune is always there (#585) |
| `AUTUNE_SLACK_APP_TOKEN` | `apps/bot` socket mode, local development only |
| `AUTUNE_SLACK_CLIENT_ID`, `AUTUNE_SLACK_CLIENT_SECRET` | core, the one-click "Add to Slack" install (#428) |
| `AUTUNE_SLACK_REDIRECT_URI` | core. The web origin's `/api/auth/slack/callback`; Slack accepts **HTTPS only**, so a local test serves `apps/web` with `next dev --experimental-https` |
| `AUTUNE_SLACK_CHANNEL_NAME` | core. The private alert channel an install creates (default `autune`; `-2`, `-3`... when taken) |
| `AUTUNE_NOTION_CLIENT_ID`, `AUTUNE_NOTION_CLIENT_SECRET` | core, a team's one-click Notion connection: a Notion public integration (#428). The token it returns is the workspace's bot's, stored encrypted |
| `AUTUNE_NOTION_REDIRECT_URI` | core. The web origin's `/api/auth/notion/callback`; must match a redirect URI of the Notion integration exactly |
| `AUTUNE_JIRA_CLIENT_ID`, `AUTUNE_JIRA_CLIENT_SECRET` | core, a team's one-click Jira connection over Atlassian OAuth 2.0 (3LO) (#458). The app is the one `external-approvals.md` says to register |
| `AUTUNE_JIRA_REDIRECT_URI` | core. The web origin's `/api/auth/jira/callback`; must match the callback URL in the Atlassian developer console exactly |
| `AUTUNE_JIRA_SCOPES` | core. What the connection asks for (default `read:jira-work write:jira-work read:jira-user offline_access`) |

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
| `AUTUNE_AUDIO_LIVE_SPEAKER_THRESHOLD` | A | Cosine similarity at or above which a live utterance joins an existing `화자 N`; below it a new one opens (0.55, provisional until the evaluation in `docs/modules/audio-live-speakers.md` §6 runs). The head count comes from `AUTUNE_AUDIO_DIARIZATION_NUM_SPEAKERS` / `_MAX_SPEAKERS`, the same hint the stored path uses |
| `AUTUNE_AUDIO_LIVE_SPEAKER_MIN_S` | A | A live utterance shorter than this takes the nearest label and may not open a speaker (1.0) |
| `AUTUNE_AUDIO_LIVE_TRANSCRIBER_IMPL` | A | `auto` (default) · `faster_whisper` · `mlx`. `mlx` is the live path on Apple silicon's GPU and needs `uv sync --all-packages --extra mlx`; `faster_whisper` follows `AUTUNE_AUDIO_DEVICE`, so an NVIDIA machine sets that to `cuda`. `auto` picks `mlx` where it can run |
| (uvicorn `--workers`) | A | **Leave at 1.** The live channel's one-session-per-meeting claim (`live/registry.py`) is per process: a second worker lets a second session onto the same meeting, and accepts an upload the other worker's open socket should have refused (409) |
| `AUTUNE_AUDIO_LIVE_MLX_MODEL` | A | The mlx-whisper weights, a Hugging Face repo. Default `mlx-community/whisper-large-v3-turbo` |
| `AUTUNE_AUDIO_ORPHAN_AFTER_HOURS` | A | A job still `queued`/`running` after this long has no worker; the sweep fails it and deletes its file. Default `6` |
| `AUTUNE_AUDIO_HF_TOKEN` | A | Hugging Face token for the gated pyannote models |
| `AUTUNE_AUDIO_DIARIZATION_NUM_SPEAKERS` | A | Exactly how many people spoke, when the room knows (#325). Unset by default: pyannote clusters freely, and a wrong number is worse than none. Deployment-wide for now; the per-meeting field comes with S10. Must be ≥ 1; the settings refuse to load otherwise |
| `AUTUNE_AUDIO_DIARIZATION_MIN_SPEAKERS` / `…_MAX_SPEAKERS` | A | Bounds instead of an exact count. Ignored when `…_NUM_SPEAKERS` is set. Each must be ≥ 1; the settings refuse to load otherwise |
| `NEXT_PUBLIC_AUTUNE_DEV_TOKEN` | A (web) | A bearer token for the browser, local only — see "A developer token for the browser" below |
| `AUTUNE_AUDIO_DIARIZATION_MODEL` | A | Default `pyannote/speaker-diarization-3.1` |
| `AUTUNE_AUDIO_DIARIZATION_DEVICE` | A | Where pyannote runs: empty (default) follows `AUTUNE_AUDIO_DEVICE`, or `cpu` · `mps` · `cuda`. Separate from `AUTUNE_AUDIO_DEVICE` because that one reaches faster-whisper, which has no Metal support. `mps` is 14.3× faster than CPU on the measured recording for a millisecond-identical result (`modules/audio/HISTORY.md` §2), but is untested under a prefork or threaded Celery worker — module E's SetFit aborts on Metal there (#329). **CUDA is unmeasured**: the millisecond agreement is CPU against MPS, and pyannote sharing VRAM with Whisper `large-v3` has not been tried; `=cpu` is the way out. **Setting it is a promise, leaving it empty is not** — an explicit device torch cannot reach refuses the task rather than running 14× slower in silence, while an empty one that cannot be used takes CPU and logs `diarization_device_unavailable`, because `AUTUNE_AUDIO_DEVICE=cuda` with a CPU torch wheel is a deployment that works today |
| `AUTUNE_AUDIO_IDENTIFICATION_THRESHOLD` | A | Cosine similarity at or above which a voice profile is offered as a speaker's candidate (0.70, provisional). Never assigns; a person confirms |
| `AUTUNE_AUDIO_SPEAKER_EMBEDDING_MAX_S` | A | Seconds of one speaker that go into their observation vector (10) |
| `AUTUNE_AUDIO_SPEAKER_EMBEDDING_MIN_S` | A | A speaker with less speech than this in a meeting gets no vector (3) |
| `AUTUNE_AUDIO_VOICE_PROFILES_ENABLED` | A | **Default `false`, and with it off no voice data is kept at all.** It gates both the worker's per-meeting observation vectors and the profile write in `assign_speaker`; a meeting reprocessed after it goes off gives its existing vectors back. Confirming a speaker still assigns them (`participants.user_id` is attendance, not biometric data), and deleting a profile is never gated by it. Off until #92's Q4 (is a voice embedding sensitive information under PIPA Article 23, and does it need its own refusable consent) is answered, or until auth exists to record that consent (#268) |
| `AUTUNE_EXTRACTION_CLASSIFIER_IMPL` | B | `local` · `hosted` · `fake` · `llm`. Default `local`. `llm` is opt-in and not signed off for real meetings — see below |
| `AUTUNE_EXTRACTION_CLASSIFIER_CHECKPOINT` | B | Pinned model, recorded with every classification. Never a floating tag. **Blank by default** — no trained checkpoint is published yet, and `local` / `hosted` refuse to start without one |
| `AUTUNE_EXTRACTION_CLASSIFIER_ENDPOINT` | B | Our own inference server. Required when `CLASSIFIER_IMPL=hosted` |
| `AUTUNE_EXTRACTION_CLASSIFIER_DEVICE` | B | `cpu` · `cuda`. Default `cpu`. Mirrors `AUTUNE_AUDIO_DEVICE` |
| `AUTUNE_EXTRACTION_LLM_API_KEY` | B | Provider key for `CLASSIFIER_IMPL=llm`, sent as a header only. **Blank by default**, and `llm` refuses to start without one. A free-tier key may let the provider keep what it is sent — dummy meetings only |
| `AUTUNE_LLM_API_KEY` | B | The same provider key under a name with no module in it, which is the name the team's deployment secret has. B reads it only when `AUTUNE_EXTRACTION_LLM_API_KEY` is blank (blank, not just unset: `.env.example` ships that line empty), so a key given to B alone still wins. **No other module reads it today** — C, D and the agent read `AUTUNE_GAP_VERIFIER_API_KEY`, `AUTUNE_CONTEXT_LLM_API_KEY` and `AUTUNE_AGENT_LLM_API_KEY`. A key selects nothing: `CLASSIFIER_IMPL` and `RESOLVER_IMPL` still decide whether B calls a provider. **It has to be a Gemini (Generative Language API) key** while `AUTUNE_EXTRACTION_LLM_BASE_URL` is at its default: the base URL picks where the text goes, not the key, so another provider's key under this name sends masked text to Google and then fails authentication there. **The free-tier rule binds whoever registers the secret** — a deployment secret under this name is the key B uses, so a free-tier key there means dummy meetings only until #392 is settled, whoever added it |
| `AUTUNE_EXTRACTION_LLM_MODEL` | B | The model `llm` calls. Default `gemini-3.8-flash`. Every classification records `llm:<model>+<fallback>` while the fallback is on |
| `AUTUNE_EXTRACTION_LLM_FALLBACK_MODEL` | B | Answers a window when `LLM_MODEL` stays unavailable (429, 5xx, timeout after retries). Default `gemini-3.5-flash-lite`; blank disables it |
| `AUTUNE_EXTRACTION_LLM_BASE_URL` | B | The provider's API root. Default Google's Generative Language API |
| `AUTUNE_EXTRACTION_LLM_TIMEOUT_SEC` | B | Timeout per request (connect and read), seconds. Default `60` — a thinking model takes 12–20 s a window, past the shared client's 10 s. A window may retry and fall back, so it can take several of these |
| `AUTUNE_EXTRACTION_NLI_IMPL` | B | `local` · `hosted` · `fake`. Default `local`. Step 4 (#12). **No `external`**: it reads an utterance's own text — see below |
| `AUTUNE_EXTRACTION_NLI_CHECKPOINT` | B | Recorded as the model version once NLI verifies a row. Never a floating tag. **Blank by default** — #172 settled on klue/roberta-base fine-tuned on KorNLI, but that checkpoint is not baked in as a silent default; `local` / `hosted` refuse to start without one |
| `AUTUNE_EXTRACTION_NLI_ENDPOINT` | B | Our own inference server. Required when `NLI_IMPL=hosted` |
| `AUTUNE_EXTRACTION_NLI_DEVICE` | B | `cpu` · `cuda`. Default `cpu`. Mirrors `AUTUNE_EXTRACTION_CLASSIFIER_DEVICE` |
| `AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE` | B | Below this, an item is a candidate rather than asserted. **Blank by default** — the number comes from the evaluation set (#10), and blank means nothing is a candidate |
| `AUTUNE_EXTRACTION_RESOLVER_IMPL` | B | `local` · `hosted` · `llm` · `fake` (#175). **Default `fake`** — unlike the classifier, since the model candidate is not yet confirmed. `llm` is the Gemini API through the same `AUTUNE_EXTRACTION_LLM_*` settings as `CLASSIFIER_IMPL=llm`: opt-in, needs `LLM_API_KEY` and no checkpoint, sends the commitment and the lines around it with the team's names replaced, and a free-tier key is for dummy meetings only. **No `external`**, same reason as the classifier |
| `AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392` | B | `true` · `false`. Default `false`. Required, as `true`, for `CLASSIFIER_IMPL=llm` / `llm_checked` or `RESOLVER_IMPL=llm`: without it B's settings refuse to load (#392). Turns nothing on by itself — see below |
| `AUTUNE_EXTRACTION_RESOLVER_CHECKPOINT` | B | Local model path/hub id, or the hosted model's recorded version. Required for `local`/`hosted` |
| `AUTUNE_EXTRACTION_RESOLVER_ENDPOINT` | B | Our own inference server. Required when `RESOLVER_IMPL=hosted` |
| `AUTUNE_EXTRACTION_RESOLVER_MODEL` | B | The model `RESOLVER_IMPL=llm` asks first. Default `gemini-3.5-flash-lite`. Its own setting, apart from `LLM_MODEL` (the classifier's) |
| `AUTUNE_EXTRACTION_RESOLVER_SECOND_MODEL` | B | Asked once when the first model's answer fails a check (a bracketed clause of its own, the deadline dropped, a runaway length), and instead of it when it stays unavailable. Default `gemini-3.8-flash`; blank turns both off. On a free-tier key it allows 5 requests a minute and 20 a day, so one meeting asks it at most 5 times (`MAX_ESCALATIONS`). Every request is cut to fit the outbound limit before it is sent -- least alike candidates first, then the farthest context -- and a line too long on its own is not sent |
| `AUTUNE_EXTRACTION_RESOLVER_DEVICE` | B | `cpu` · `cuda`. Default `cpu`. Mirrors `AUTUNE_EXTRACTION_CLASSIFIER_DEVICE` |
| `AUTUNE_EXTRACTION_EMBEDDER_IMPL` | B | `local` · `fake` (#175, #366). **No `hosted` yet.** Default `fake`, same reason as `RESOLVER_IMPL` |
| `AUTUNE_EXTRACTION_EMBEDDER_CHECKPOINT` | B | Default `nlpai-lab/KURE-v1` — module D's already-shipped choice, not a candidate awaiting evaluation |
| `AUTUNE_EXTRACTION_EMBEDDER_DEVICE` | B | `cpu` · `cuda`. Default `cpu` |
| `AUTUNE_EXTRACTION_RESOLVER_MIN_SIMILARITY` | B | Below this cosine similarity to its own context window, a resolved sentence is ungrounded. **Blank by default** — no embedding model has been run against a labelled set yet, and blank skips the check entirely |
| `AUTUNE_EXTRACTION_DEV_ROUTES` | B | `true` mounts the unauthenticated page for connecting Notion by hand, and only when `AUTUNE_ENV=local` too. Default `false`. Deleted with S28 (#401) |
| `AUTUNE_GAP_RISK_THRESHOLD` | C | Default `0.7`. At or above is `high`, the only severity surfaced |
| `AUTUNE_GAP_MEDIUM_THRESHOLD` | C | Default `0.5`. Down to here is `medium`, below it `low` |
| `AUTUNE_GAP_DEFAULT_TEMPLATE` | C | Default `general`. Which domain template a meeting nobody chose one for is held to |
| `AUTUNE_GAP_PARTIAL_CENTRALITY` | C | Default `0.4`. A matched topic below this makes the item *partial* rather than covered |
| `AUTUNE_GAP_PARTIAL_DAMPING` | C | Default `0.7`. What a partial finding's risk score is multiplied by |
| `AUTUNE_GAP_WEIGHT_TEMPLATE` · `_COVERAGE` · `_PARTICIPATION` | C | Defaults `0.4` · `0.4` · `0.2`. The three risk inputs, relative; renormalised over whichever could be measured |
| `AUTUNE_GAP_NER_IMPL` | C | `spacy` (default) · `fake`. **No `external`** — see below |
| `AUTUNE_GAP_NER_MODEL` | C | Default `ko_core_news_lg`. The pipeline **name**; the version comes from the pinned wheel and is recorded per row |
| `AUTUNE_GAP_RELATION_IMPL` | C | `rule` (default) · `gemini`. **`gemini` is external** and opt-in: the rules, then the pairs they decline go to Google — see below. Uses the `AUTUNE_GAP_VERIFIER_*` key, model and URL |
| `AUTUNE_GAP_RELATION_ASSIST_MAX_UTTERANCES` | C | `30`. At most this many utterances of one meeting leave per run under `gemini` |
| `AUTUNE_GAP_EMBEDDER_IMPL` | C | `off` (default) · `local` · `fake`. Reads template comparison's speech by meaning. **No `external`**, same reason as the entity extractor; `local` needs the `local-models` extra |
| `AUTUNE_GAP_EMBEDDER_CHECKPOINT` | C | Default `nlpai-lab/KURE-v1` — module D's and B's choice |
| `AUTUNE_GAP_EMBEDDER_DEVICE` | C | `cpu` (default) · `cuda`. Never inferred from the machine |
| `AUTUNE_GAP_SEMANTIC_FLOOR` | C | Default `0.55`. The cosine an utterance needs with an item's nearest example to count as saying it |
| `AUTUNE_GAP_SEMANTIC_MARGIN` | C | Default `0`. How far the winning item must lead the runner-up |
| `AUTUNE_GAP_VERIFIER_IMPL` | C | `off` (default) · `fake` · `gemini`. Checks the utterances the embedder is unsure of. **`gemini` is external** and opt-in — see below. Needs the embedder on |
| `AUTUNE_GAP_VERIFIER_API_KEY` | C | Provider key for `gemini`, sent as a header only. **Blank by default**; `gemini` refuses to start without one |
| `AUTUNE_GAP_VERIFIER_MODEL` · `_FALLBACK_MODEL` | C | Defaults `gemini-3.8-flash` · `gemini-3.5-flash-lite`, module B's. Blank fallback disables it |
| `AUTUNE_GAP_VERIFIER_BASE_URL` · `_TIMEOUT_SEC` | C | Google's Generative Language API root · `60` |
| `AUTUNE_GAP_VERIFY_CONFIDENT_SCORE` · `_CONFIDENT_LEAD` | C | Defaults `0.6` · `0.05`. An item winning by both is taken without asking; a background win by the lead is dismissed without asking |
| `AUTUNE_GAP_VERIFY_CANDIDATE_SCORE` · `_CANDIDATES` · `_EXAMPLES` | C | Defaults `0.45` · `3` · `2`. Which items one question offers, and how many example sentences each carries |
| `AUTUNE_GAP_VERIFY_MAX_UTTERANCES` | C | Default `30`. At most this many utterances of one meeting are sent per run; the rest keep the embedding's answer |
| `AUTUNE_GAP_RESCORE_MAX_ATTEMPTS` | C | Default `5`. Failed rescores in a row at one grouping of people before the ten-minute sweep leaves the meeting until the grouping moves (#516) |
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
| `AUTUNE_CONTEXT_LINK_SIMILARITY_THRESHOLD` | D | Assert a topic link at or above this dense segment similarity (cosine). Default `0.74`, tuned in eval |
| `AUTUNE_CONTEXT_LINK_CONFIDENCE_THRESHOLD` | D | Also assert at or above this re-ranker score. Default `0.6` |
| `AUTUNE_CONTEXT_LINEAGE_MATCH_THRESHOLD` | D | Decision-to-thread match cutoff (cosine). Default `0.65`, tuned in eval |
| `AUTUNE_CONTEXT_PUBLISH_TIMEOUT_S` | D | Wait for B before publishing. Default `600` |
| `AUTUNE_CONTEXT_MAX_TOPIC_LINK_NOTICES` | D | Individual topic-link Slack messages per meeting before the rest roll up into one notice. Default `3` |
| `AUTUNE_CONTEXT_BRIEF_LEAD_MINUTES` | D | Minutes before a scheduled meeting's start that its pre-meeting brief is posted. Default `10` |
| `AUTUNE_CONTEXT_WARM_MODELS_ON_WORKER_INIT` | D | `true` only on workers consuming `cpu_heavy`. Default `false` |
| `AUTUNE_CONTEXT_ENGINE_MODE` | D | `classic` (default) · `llm` · `hybrid`. `llm` swaps the topic-link, decision-thread and change-type judgements for an external LLM; `hybrid` is `classic` with the LLM vetoing links it is about to assert — see "Engine mode" in `docs/modules/context.md`. Both send masked excerpts to a third party; a team decision before either is set on real meetings |
| `AUTUNE_CONTEXT_LLM_IMPL` | D | `openai` · `gemini` · `anthropic` · `fake`. **No default**: which provider gets meeting excerpts is a team decision. Only read when `ENGINE_MODE=llm` |
| `AUTUNE_CONTEXT_LLM_API_KEY` | D | Secret, the chosen provider's key. Never commit it, never log it |
| `AUTUNE_CONTEXT_LLM_ENDPOINT` | D | Empty = the provider's own. Set for a proxy, Azure OpenAI or an OpenAI-compatible server (use `LLM_IMPL=openai`) |
| `AUTUNE_CONTEXT_LLM_MODEL` | D | Required for `openai` and `gemini` (model names turn over faster than the repo); `anthropic` defaults to `claude-opus-5-5`. Recorded in every row's version column |
| `AUTUNE_CONTEXT_LLM_EFFORT` | D | Empty (default) sends nothing. `openai`: `reasoning_effort` (a non-reasoning model rejects it). `anthropic`: `output_config.effort`. `gemini`: not sent. No provider is sent a `temperature` |
| `AUTUNE_CONTEXT_LLM_MAX_TOKENS` | D | Default `4096`. Covers a reasoning model's thinking as well as the JSON answer; a reply cut off by it is unusable |
| `AUTUNE_CONTEXT_LLM_TIMEOUT_S` | D | HTTP timeout, seconds. Default `60.0` |
| `AUTUNE_CONTEXT_LLM_CONCURRENCY` | D | Verdicts in flight at once per batch. Default `4`; lower it on a 429 |
| `AUTUNE_CONTEXT_LLM_SNIPPET_CHARS` | D | Longest excerpt sent per side of a pair. Default `1200`; `check_outbound` refuses a request over 4000 characters in total |
| `AUTUNE_CONTEXT_LLM_TOPIC_CANDIDATES` | D | Past meetings the LLM is asked about per topic. Default `5`; each is one paid call |
| `AUTUNE_CONTEXT_LLM_THREAD_CANDIDATES` | D | Decision threads the LLM is asked about per new decision. Default `3` |
| `AUTUNE_CONTEXT_LLM_LINK_THRESHOLD` | D | LLM probability of "same topic" at or above which a link is asserted. Default `0.5` |
| `AUTUNE_CONTEXT_LLM_PENDING_FLOOR` | D | Below this no link row is written at all (in `hybrid`: a veto drops the link). `0` = a veto only demotes to `pending`. Default `0.25` |
| `AUTUNE_CONTEXT_LLM_MATCH_THRESHOLD` | D | Confidence at or above which "same decision" threads a decision. Default `0.5` |
| `AUTUNE_INTELLIGENCE_AGGREGATE_TIMEOUT_SECONDS` | E | Wait for B/C/D before aggregating without the rest. Default `600` |
| `AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL` | E | `local` (default) · `fake`. **No `external`, no `hosted`** — see below |
| `AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_BACKBONE` | E | Sentence-embedding backbone SetFit fits its few-shot head onto. Default `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| `AUTUNE_INTELLIGENCE_WARM_MODELS_ON_WORKER_INIT` | E | `true` only on workers consuming aggregation tasks — builds the gap classifier and the misalignment predictor at startup. Default `false` |
| `AUTUNE_INTELLIGENCE_MISALIGNMENT_PREDICTOR_IMPL` | E | `heuristic` (default) · `local` (XGBoost fit on labeled history, heuristic until there is enough). No external option. Making `local` the default is gated on #450 — see `../modules/intelligence.md` |
| `AUTUNE_INTELLIGENCE_MISALIGNMENT_REFIT_HOURS` | E | How long a fit (or fallback) is kept before `local` refits. Default `24` |
| `AUTUNE_INTELLIGENCE_WEB_BASE_URL` | E | Web app origin, e.g. `https://autune.example.com`. The meeting report's details button links to `<this>/meetings/<id>`; unset, the report is posted without the button. Default unset |

Notion, Jira and Calendar credentials are **not** environment variables. Each
team configures its own on screen S28 and they are stored encrypted in
`team_integrations` — read them with `autune_core.load_integration`, never from
settings. See `../architecture/data-model.md`.

Every new variable goes into `.env.example` with a comment and into this table.
A variable that exists only in someone's local `.env` will break the next
person's setup.

### The classifier's one external option is opt-in

`AUTUNE_EXTRACTION_CLASSIFIER_IMPL` accepts `local`, `hosted`, `fake` and `llm`.
Module B classifies every utterance in a meeting, so an external implementation
sends the whole meeting's text to somebody else's model — which section 6 of
`../architecture/privacy.md` makes a design conversation rather than a value you
can set. Module B added `llm` as an opt-in after the 2026-09-23 mentoring, and
the conversation is #392. Until #392 is settled:

- `llm` is never the default, and nothing selects it for you.
- **It has to be switched on twice.** With `AUTUNE_EXTRACTION_CLASSIFIER_IMPL`
  set to `llm` or `llm_checked`, or `AUTUNE_EXTRACTION_RESOLVER_IMPL` set to
  `llm`, module B's settings refuse to load unless
  `AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392=true` is set as well — every B route
  and task refuses, not only the model call, and the error names the variable.
  "Dummy meetings only" and "a paid key" are rules the code cannot check; the
  flag makes sending speech to a provider something a deployment says twice.
  It turns nothing on by itself, it is not keyed on `AUTUNE_ENV` (whose default
  is `local`), and deleting it is the migration once #392 is decided.
- Use it on dummy meetings only. A free-tier key may let the provider keep what
  it is sent; a real meeting needs a paid key and #392's answer.

What it sends is utterance text as module A masked it and a fixed instruction —
no speaker, no id, no meeting title — in windows under the 4,000-character
outbound cap, through `autune_integrations.HttpClient` like `hosted`. Module A
masks resident registration, card, phone and account numbers and email
addresses, and has no pattern or model for names, **so `llm` replaces the
meeting team's names itself before sending (#411):** each member's display
name, and the given name of a three-syllable Korean name ("김민경" and "민경"),
becomes `[사람N]`. A display name comes from the account's `name` claim and is
often spaced ("박 재경", "재경 박"), so a Hangul name of two words is also matched
joined and swapped ("박재경", "재경박") and by its given name ("재경") — the word
of two syllables or more beside a one-syllable surname; with two longer words
only the joined forms. Whichever form matched, the same person gets the same number
within one meeting's requests. The classifier's placeholders are never stored and
never mapped back: it answers with labels only, and the database and Notion keep
the text as it was. The `llm` reference resolver is the one that maps back -- its
answer is a sentence stored as a description, so each `[사람N]` is restored to the
name it stood for, and an answer holding a placeholder that was never sent is
dropped for the raw quote.

What still goes out, and is the exposure #392 and #92 ask about:

- names not on the team's roster — people outside the team, nicknames, English
  names and names the speech recogniser misheard;
- a roster name that is also an ordinary word ("하늘", "보람") is replaced where
  it is only a word — the cost is classification accuracy, not data;
- the reference resolver's lines when `RESOLVER_IMPL=llm`, which goes through the same name replacement and the same outbound guard but is an additional request per commitment — a name not on the roster leaves in them too; the `local` and `hosted` resolvers send nothing to a provider. **It sends more than the smallest window**: the four lines before a commitment (or before a decision's first turn) and two after, *and* up to eight lines from anywhere else in the meeting that share its words, so that the summary can draw on what was said far from it. Only consenting speakers' lines, masked, without speaker or id; the ids stay on our side and come back as line numbers. A team that wants only the smallest window keeps `RESOLVER_IMPL=fake`.

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

### NLI (step 4) has no external option, and the same extra

`AUTUNE_EXTRACTION_NLI_IMPL` accepts `local`, `hosted` and `fake` — not the
classifier's `llm`. Step 4 (#12) reads a commitment or ambiguous utterance's
own text, so an external implementation is the section 6 question #392 is
settling for the classifier; the classifier's opt-in does not extend to NLI.
`local` needs the same `local-models` extra as the classifier
(`transformers`/`torch` are shared); no separate `uv sync` is needed if you
already installed it for the classifier.

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
conversation rather than a value you can set.

Relation extraction is the exception, and `AUTUNE_GAP_RELATION_IMPL=gemini` is
it. A relation is read off one clause, so the hard cases can be sent without
sending the meeting: the marker rules run in process first, and only an
utterance holding a pair they decline — `는데`/`지만` glue, a bare `의`, a
reason the resolution guard read as resolved — goes to Google, one line each as
module A stored it. **Names, and on the batch path numbers read out as words,
are not masked** and go with it. It goes through `packages/integrations` so
`check_outbound` sees the request body. Same standing as the template verifier
below: never the default, dummy meetings only until the team decides. The
default, `rule`, sends nothing. See "Relation assistance" in
`../modules/gap.md`.

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

### Module C's template verifier is opt-in and external

`AUTUNE_GAP_VERIFIER_IMPL=gemini` sends the utterances the embedder could not
decide, one line each as module A stored them, to Google — with their candidate
checklist items and nothing else from the meeting. **Names, and on the batch
path numbers read out as words, are not masked** and go with them. Same standing as module B's `llm` classifier (#392):
never the default, dummy meetings only until the team decides, and a free-tier
key may let the provider keep what it is sent. What a request carries is listed
in `../modules/gap.md`, "Verifying what the embedder was unsure of".

### The gap classifier has no external or hosted option

`AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL` accepts `local` and `fake`, and
nothing else. It classifies gaps across a team's whole meeting history —
exactly the aggregation section 3 of `../architecture/privacy.md` asks module
E to be careful with — so an external implementation is a design conversation,
not a config value. That much is the same reasoning module C gives for its
entity extractor (`AUTUNE_GAP_NER_IMPL`) and module B for its NLI
(`AUTUNE_EXTRACTION_NLI_IMPL`). B's classifier is the exception: it has an
opt-in external `llm` (`AUTUNE_EXTRACTION_CLASSIFIER_IMPL` above), never the
default and pending #392. None of this says anything about `hosted`, which B
does have on both of its settings.

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

The misalignment predictor's `local` implementation
(`AUTUNE_INTELLIGENCE_MISALIGNMENT_PREDICTOR_IMPL`) comes from the same extra, and
on macOS it needs one thing more. XGBoost's macOS wheel links
`@rpath/libomp.dylib` without bundling it, so the install succeeds and
`import xgboost` then fails:

```bash
brew install libomp   # macOS only, and only for the predictor
```

That failure is an `XGBoostError`, not an `ImportError`, so it passes straight
through the check that would otherwise name the extra — the message you get is
XGBoost's own and it names the library. Nothing else in `local-models` needs
OpenMP, so a Mac running only the gap classifier can skip this.

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

## A developer token for the browser

Every route that matters takes `CurrentUser`. Signing in with Google (S01) sets
a session cookie and needs nothing below. Without Google credentials configured,
or to act as several users without several Google accounts, module A's dev
router issues a token:

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

**A session wins over the dev token (#440).** Once the app finds a session
cookie (`/api/auth/me`), it stops attaching the dev token, so every call runs as
the signed-in person. To act as the dev-token user, use a tab with no session:
a private window, or delete the `autune_session` cookie. Calls made
outside the app (curl, Swagger) still send whatever `Authorization` header they
are given, and the API reads that before the cookie.

## Local privacy hygiene

`AUTUNE_AUDIO_TEMP_DIR` holds real audio while a task runs. It is gitignored and
cleared at the end of every task. Do not point it at a synced folder, and do not
keep test recordings of real meetings on disk. See
`../architecture/privacy.md`.

**The API and the worker must see the same directory on the same filesystem.**
The upload endpoint writes the recording and the worker adopts it by job id, so
a deployment that gives the two processes different storage breaks the handover:
the worker finds nothing, and the file the endpoint wrote is left with nobody
to delete it. Locally both run on the host from the same checkout (the two
commands at the top of this file), so they share `AUTUNE_AUDIO_TEMP_DIR` by
construction: `AudioSettings` reads `.env` relative to the working directory,
and both are started from the repository root. `infra/docker-compose.yml` runs
only PostgreSQL and Redis, and no volume is involved.

Containerising either process means both must see one **local** directory at
that path: a bind mount of the host directory (the only option when one of the
two stays on the host), or a volume shared by both containers on the same host
— tmpfs or the local driver, never a network- or cloud-backed driver. This is
the scratch directory `privacy.md` section 1 requires, not the "mounted volume"
it forbids: files in it are deleted by `storage.adopt` and `service.sweep_orphans`, and
the volume must not outlive the host. Keep that true, or change the handover
rather than the path (`privacy.md` section 1, decision #275).

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
| Whisper is very slow | Running on CPU. Set `AUTUNE_AUDIO_DEVICE=cuda` or use a smaller model locally. That variable is also what diarization inherits, and `uv sync` installs a CPU torch wheel on Windows while faster-whisper reaches the GPU through CTranslate2 — so diarization stays on CPU there and says so in the log. Set `AUTUNE_AUDIO_DIARIZATION_DEVICE=cuda` to require the GPU for it too |
| Diarization takes minutes on a Mac | It is on CPU, which is where it stays unless told otherwise. `AUTUNE_AUDIO_DIARIZATION_DEVICE=mps`, in a `--pool=solo` worker (#329) |
| Generated TS types are stale in CI | Run `pnpm run gen:contracts` and commit the output |
