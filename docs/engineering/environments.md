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
and sending mail as them are separate consents, given after signing in and
described next. Nothing reads
`AUTUNE_GOOGLE_CALENDAR_CREDENTIALS`, which this page used to name here.

### A person's own Google grant

| Variable | Example | Notes |
| --- | --- | --- |
| `AUTUNE_GOOGLE_INTEGRATION_CLIENT_ID` | | A second Google Cloud OAuth client, for what a person connects after signing in — their own calendar (#435) and sending an invitation from their own Gmail (#552). Optional: blank, the sign-in client does both |
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

**The scopes a person's grant asks for** are `calendar.events` (the calendar
card) and `gmail.send` (an invitation mailed from the inviter's own address,
#552), each its own consent and its own grant. Both are *sensitive* scopes: the
OAuth consent screen of the client that asks for them -- the integration client
when it is set, the sign-in client otherwise -- must list them, and until the
app is verified by Google only the consent screen's test users can grant them.
A person who is not a test user gets Google's refusal and the screen says the
connection failed; the invitation link can still be copied. `gmail.send` reads
nothing; reading a mailbox (`gmail.readonly`, a *restricted* scope) is #431's
question and is not asked for.

**Two cookies, two jobs.** The session cookie is the signed session (7 days,
`HttpOnly`, `SameSite=Lax`, `Secure` outside local). The OAuth state cookie
lives only for the 600 seconds of one sign-in and holds the OAuth `state`: the
callback refuses a request whose cookie does not match the `state` in the
query, so a callback URL opened in somebody else's browser cannot sign them in
as whoever started it. Redis proves a state was issued; the cookie is what
proves to whom.

Their names depend on the environment. Where cookies are `Secure` (everywhere
but local) they are `__Host-autune_session` and
`__Host-autune_oauth_state_<provider>` (`google`, `jira`, `notion`, `slack`),
with `Path=/` and no `Domain`: a browser takes a `__Host-` cookie only on those
terms and refuses one a sibling name on the same site (`autune.cloud` beside
`app.autune.cloud`) tries to set, and the unprefixed names are not read there at
all. The prefix needs `Path=/`, so the state cookies are told apart by name,
not by the callback's path. Local development is plain HTTP and keeps
`autune_session` and `autune_oauth_state`, the callback's path included. Moving
to the prefixed names signs everyone out once.

A request that changes state, or opens the live WebSocket, and carries an
`Origin` whose host is neither the API's own `Host` nor one of
`AUTUNE_CORS_ALLOWED_ORIGINS` is refused with 403 (`origin_guard.py`). A request
with no `Origin` goes through: Slack posts to `/api/slack/events` with none and
is authenticated by its signature. The check is off in local development.
`packages/core/src/autune_core/auth_router.py` has the reasoning.

**Google flows also carry PKCE** (#704). Sign-in and the personal connects
send an S256 `code_challenge`; the verifier waits in the same Redis entry as
the `state` and goes out only with the code exchange, so a code lifted off the
redirect is worthless elsewhere, even with the client secret. Nothing to
configure: Google accepts PKCE from a web client as it is.

**Atlassian, Notion and Slack are sent no challenge, and are not going to be**
(#767). Each reason below is from the provider's own documentation as read
on 2026-10-06; none was probed with a real app, so what a provider does with
a challenge it was not asked to accept is not known. `state` with the cookie
above binds their callbacks, as before.

- **Slack** has PKCE, as an app setting, for apps that cannot keep a secret:
  turning it on "marks your app as a public client, which is a one-way
  operation", the exchange then goes without `client_secret`, and every
  refresh token issued to the app expires in 30 days. Autune's Slack app is
  a server that keeps its secret, so the setting would weaken it and change
  token lifetimes for every team's installation. It stays off.
- **Atlassian** (Cloud, OAuth 2.0 3LO at `auth.atlassian.com`) documents no
  PKCE parameter: the authorization request takes `audience`, `client_id`,
  `scope`, `redirect_uri`, `state`, `response_type` and `prompt`, and the
  exchange takes `client_secret`. Atlassian's Data Center products do
  document `code_challenge`; that is another product and endpoint.
- **Notion** (public integration) documents none either: the authorization
  request takes `client_id`, `redirect_uri`, `response_type`, `owner` and
  `state`, and the exchange is HTTP Basic with the client's id and secret.
  Notion documents PKCE for its MCP server's OAuth, which is not this flow.

A challenge sent where it is not documented would at best be ignored, and
the code would then claim a protection that is not there.

**A connect that stores a token checks the encryption key before it spends
the code** (#593, #704). Calendar, Jira, Notion and Slack callbacks all fail
on a missing or broken `AUTUNE_ENCRYPTION_KEY` before the provider is asked,
so no grant is issued that nothing keeps; the screen shows the connect as
failed. Their start endpoints check it too, so nobody is sent through a
consent screen that cannot succeed.

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
| `NEXT_PUBLIC_LIVE_URL` | `wss://api.example.com` | Where the live transcript socket opens. Empty: the page's own origin. Set it where a proxy in front of the site drops the WebSocket `Upgrade` header; the page then fetches a ticket over its own origin for the socket's `hello`, because the session cookie does not reach another host. The ticket opens that meeting's socket once, within 60 s, and no other route (`modules/audio/src/autune_audio/live/tickets.py`). Tickets, like live sessions, are held in the API process's memory, so the API runs as one process |
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
| `AUTUNE_AUDIO_HEARTBEAT_INTERVAL_S` | A | How often a running transcription writes its heartbeat and checks whether it was cancelled. Default `30` |
| `AUTUNE_AUDIO_STALL_AFTER_S` | A | A running transcription whose heartbeat is older than this has no worker and may be restarted. Default `120` |
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
| `AUTUNE_EXTRACTION_NLI_IMPL` | B | `local` · `hosted` · `fake` · `llm`. Default `local`. Step 4 (#12). `llm` is the Gemini API: opt-in, needs `LLM_API_KEY` and `LLM_ACKNOWLEDGED_392`, and sends the masked text of the meeting's ambiguous utterances — see below |
| `AUTUNE_EXTRACTION_NLI_CHECKPOINT` | B | Recorded as the model version once NLI verifies a row. Never a floating tag. **Blank by default** — #172 settled on klue/roberta-base fine-tuned on KorNLI, but that checkpoint is not baked in as a silent default; `local` / `hosted` refuse to start without one |
| `AUTUNE_EXTRACTION_NLI_ENDPOINT` | B | Our own inference server. Required when `NLI_IMPL=hosted` |
| `AUTUNE_EXTRACTION_NLI_DEVICE` | B | `cpu` · `cuda`. Default `cpu`. Mirrors `AUTUNE_EXTRACTION_CLASSIFIER_DEVICE` |
| `AUTUNE_EXTRACTION_NLI_MODEL` | B | The model `NLI_IMPL=llm` asks. Default `gemini-3.8-flash`. Its own setting, apart from `LLM_MODEL` (the classifier's): step 4 is a few utterances a meeting, usually one request |
| `AUTUNE_EXTRACTION_NLI_FALLBACK_MODEL` | B | Asked when `NLI_MODEL` stays unavailable. **Blank by default**: no second model answers step 4 unless one is named |
| `AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE` | B | Below this, an item is a candidate rather than asserted. **Blank by default** — the number comes from the evaluation set (#10), and blank means nothing is a candidate |
| `AUTUNE_EXTRACTION_RESOLVER_IMPL` | B | `local` · `hosted` · `llm` · `fake` (#175). **Default `fake`** — unlike the classifier, since the model candidate is not yet confirmed. `llm` is the Gemini API through the same `AUTUNE_EXTRACTION_LLM_*` settings as `CLASSIFIER_IMPL=llm`: opt-in, needs `LLM_API_KEY` and no checkpoint, sends the commitment and the lines around it with the team's names replaced, and a free-tier key is for dummy meetings only. **No `external`**, same reason as the classifier |
| `AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392` | B | `true` · `false`. Default `false`. Required, as `true`, for `CLASSIFIER_IMPL=llm` / `llm_checked`, `RESOLVER_IMPL=llm`, `SUMMARY_IMPL=llm`, `TITLE_IMPL=llm` or `NLI_IMPL=llm`: without it B's settings refuse to load (#392). Turns nothing on by itself — see below |
| `AUTUNE_EXTRACTION_TITLE_IMPL` | B | `none` · `llm`. **Default `none`**: a card and a decision row then show the sentence cut at twenty characters, as before. `llm` asks the Gemini API, through the same `AUTUNE_EXTRACTION_LLM_*` settings, for a title of twenty characters or fewer for each action item and decision an extraction wrote — up to twenty rows to a request, so one request for a meeting of twenty rows or fewer, after the extraction. Opt-in like every cloud step: needs `LLM_API_KEY` and `LLM_ACKNOWLEDGED_392`, and a free-tier key is for dummy meetings only. It sends only sentences the pipeline wrote (never one a person typed, edited or reworded), masked, with the team's names replaced and a decision's owner and deadline left off; a title that fails the rules in `pipeline/title.py` is dropped. A title is shown on Autune's screens and leads the row's copy in Jira, Notion, a calendar, a Slack line and a project's minutes (privacy.md section 6) |
| `AUTUNE_EXTRACTION_TITLE_MODEL` | B | The model `TITLE_IMPL=llm` asks first. Default `gemini-3.5-flash-lite` |
| `AUTUNE_EXTRACTION_TITLE_FALLBACK_MODEL` | B | Answers when `TITLE_MODEL` stays unavailable. Default `gemini-3.8-flash`. Blank disables the fallback |
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
| `AUTUNE_EXTRACTION_MATERIAL_UPLOAD` | B | `true` takes a file uploaded to a team's 자료 and keeps its masked text (#817). Default `false`: off, the upload route answers a bare 404. Off by default, and no code turns it on. Whether a deployment turns it on is decided on #817. `true` needs `AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_IMPL` to be `local` or `hosted`: with `fake`, module B's settings refuse to load, so the api and every Celery process stop at start with a message naming both variables, in any `AUTUNE_ENV` (#1262). `alembic upgrade heads` still runs. |
| `AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_IMPL` | B | `local` · `hosted` · `fake`. Default `fake`. Makes an uploaded material's vectors and a search question's vector (#817); `local` is KURE-v1 in this process with `AUTUNE_EXTRACTION_EMBEDDER_CHECKPOINT` and `_DEVICE`, `hosted` the same model on our own inference server (`POST /embed`, masked text only; a vector not 1024 wide is refused), recording the same checkpoint as its version. `fake` finds by shared characters, not meaning. A search reads only vectors of the embedder it asks with. With `hosted` the server is asked first (`GET /info`): unless it answers the value of `AUTUNE_EXTRACTION_EMBEDDER_CHECKPOINT` as its `model_version`, string for string, the embedder does not start and no text is sent. |
| `AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_ENDPOINT` | B | Required when `MATERIAL_EMBEDDER_IMPL=hosted`. **It must be the address of an inference server Autune itself runs, and the code does not check that**: the masked text of every uploaded piece and of every search question is posted to whatever address is set. Two sentences hold only while it is ours -- `privacy.md` ("to nothing outside Autune's own servers") and the legal notice, 제4조 ⑤ (`apps/web/src/app/legal/content.ts`: "외부 서비스에 전송하지 않으며"). The address of an outside embedding service is therefore not a setting: it is a change to both, decided and written there first. Until #1250 is decided `hosted` is not set on the dev site or in production, whatever the address; a trial on a developer's own machine with made-up text is not barred. |
| `AUTUNE_EXTRACTION_MATERIAL_SEARCH_MIN_SIMILARITY` | B | Below this cosine similarity to the masked question, a material is not a search hit; with none left, the answer is empty and says nothing close enough was found (#817). Default `0.52` for pieces of 200 characters (ADR 0011: the lowest floor that keeps the questions nothing answers at the rate 0.5 gave with pieces of 500; 0.51 without the ten reports of that run, so it moves by 0.01 with a shelf, and it is not yet measured through the product). The earlier measurement, for pieces of 500 and a default of `0.5`, was for KURE-v1 (`local` or `hosted`) on 30 made-up documents and 160 questions: an answering material scored 0.45–0.80 and one that does not 0.16–0.66, so no floor separates them; at 0.5, 87 of 90 answers were kept and small talk found nothing. A question on a material's subject that the material does not answer still passes, and more unrelated questions find something as the shelf grows. **Not measured on a team's materials — tune it on real questions with known answers.** Meaningless for `fake`, whose tests set it themselves |
| `AUTUNE_EXTRACTION_MATERIAL_CHUNK_CHARS` | B | The longest piece an upload's masked text is cut into, one stored row each (#817, ADR 0011). Default `200`, 50–1000. Measured from 30 to 500 characters on made-up text: from 50 up the material was found about as often, what the excerpt held was best at 100–200, and 500 was not the best. A smaller piece costs rows, not embedding time. Applies to uploads from then on; stored rows keep their cut, and the floor goes with the size. |
| `AUTUNE_EXTRACTION_MATERIAL_HEADING_PATH` | B | `true` (default) embeds each piece with the headings above it (a markdown `#` line or a short numbered line such as `2.1`); the stored piece and every excerpt stay the piece alone (#817, ADR 0011). `false` embeds the piece alone. Piece boundaries are the same either way. |
| `AUTUNE_EXTRACTION_MATERIAL_EMBED_OVERLAP_CHARS` | B | How many characters of the piece before it are put in front of a piece in the text that is embedded, 0–100, within one section (#817, ADR 0011). Default `20`. Embedded only: the stored piece, every excerpt and the same-text check are the piece alone. `0` embeds the piece alone. |
| `AUTUNE_EXTRACTION_MATERIAL_SEARCH_EXCERPTS` | B | How many excerpts one search hit carries, 1–3 (#817, ADR 0011, open decision 4). Default `1`: one excerpt per material is what #817 10(b) answered. **Do not set it above 1 before open decision 8 of ADR 0011 is answered**: it is the only thing between a deployment and 600 characters of one material per hit (3,000 over five hits). Above 1 a hit also carries excerpts of its next-nearest pieces (`more_excerpts`), each at most 200 characters and screened like the first. |
| `AUTUNE_EXTRACTION_MATERIAL_SEARCH_MAX_BELOW_BEST` | B | A material over the floor is still not a search hit when its nearest piece is more than this far, in cosine similarity, below the best material's (#817); `2` turns the cut off. Default `0.05`, measured for KURE-v1 (`local` or `hosted`) on 200 made-up files — prose, `.xlsx` tables, `.pptx` slides, long files, look-alike reports — and 220 questions one of them answers: at 200 files the floor alone kept 207 answers and showed 2.7 materials that do not answer beside them; with the cut, 205 and 0.6. The same margin held from 30 to 200 files. The best material always stays, so it does nothing for a question no material answers. **Not measured on a team's materials — tune it with the floor.** Meaningless for `fake`, whose tests turn it off |
| `AUTUNE_EXTRACTION_MATERIAL_SEARCH_WORD_MATCH_MIN_SIMILARITY` | B | A piece under the floor is still a search hit when it holds every word of the masked question (two letters or digits and more, as a substring) and scores this or more, within the cut of the best hit; a material whose title holds every word is a hit whatever its pieces score (#817). `2` turns the word match off, the title's too. Default `0.3`, measured for KURE-v1 (`local` or `hosted`) on 200 made-up files, not on a team's own: one-word questions kept 64 of 70 answers instead of 45, two-word ones 67 instead of 62, a file's own title 198 of 200 instead of 186; a one-word question shows 0.56 materials that do not answer beside it instead of 0.27; unrelated questions find something as often as before but for 7 instead of 5 of 30 one-word workplace ones. No bound at all returned the same answers, `0.35` one fewer. A common word is in many files and titles; the words are matched in the process and are in no statement |
| `AUTUNE_EXTRACTION_MATERIAL_SEARCH_TOOL` | B | `true` lets the assistant's `extraction.find_materials` search a team's materials in a team chat; the member is shown masked excerpts under the answer and no model is given a material's title or text (#817). Default `false`: off, it searches nothing. Whether a deployment turns it on is decided on #817 |
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
| `AUTUNE_CONTEXT_BRIEF_SIMILARITY_THRESHOLD` | D | The pre-meeting brief recaps a past meeting by topic at or above this dense similarity to its title and agenda. Default `0.60`, set on the brief-selection eval |
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
the conversation is #392. It has settled how the team's dev site -- the
deployment built from the `dev` branch -- runs it, and not yet whether a real
service may:

- `llm` is never the default, and nothing selects it for you.
- **It has to be switched on twice.** With `AUTUNE_EXTRACTION_CLASSIFIER_IMPL`
  set to `llm` or `llm_checked`, or `AUTUNE_EXTRACTION_RESOLVER_IMPL`,
  `AUTUNE_EXTRACTION_SUMMARY_IMPL`, `AUTUNE_EXTRACTION_TITLE_IMPL` or
  `AUTUNE_EXTRACTION_NLI_IMPL` set to `llm`, module B's settings refuse to
  load unless
  `AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392=true` is set as well — the worker and
  the API do not start (the API with every other module, since it imports each
  router), and the error names the variable.
  Which meetings go through and whether the key is a paid one are rules the
  code cannot check; the flag makes sending speech to a provider something a
  deployment says twice.
  It turns nothing on by itself, and deleting it is the migration once #392 is
  decided. It is not keyed on `AUTUNE_ENV`: `.env.example` ships
  `AUTUNE_ENV=local`, so a deployment made from that file would be the one let
  through.
- **On a free-tier key, dummy meetings only.** A free-tier key may let the
  provider keep what it is sent.
- **On the dev site, the team's own meetings only** (#392, 2026-10-05, out of
  the review of #818; the four other owners approved #818 and #748 under it).
  Its keys are paid. A meeting with a participant from outside the team is not
  uploaded there: a name that is not on the team's roster is not replaced
  (below) and leaves as it was spoken, and that is accepted for the team's own
  people only. Nothing in the code knows who was in the room, so this binds
  whoever uploads, and the upload form and the live gate say it above their
  consent row on any server that sets a cloud implementation
  (`../modules/extraction.md`, `GET /cloud-model`). The owners agreed to it
  for the 10-09 gate and the 10-12 demo; whether the dev site keeps `llm`
  after the demo is #392's to say.
- **A real service: not decided.** Anybody else's meeting needs #392's answer,
  and with it an answer to the overseas-transfer question it put on #92.

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
within one meeting's requests. **Whose names:** the members of the team that
held the meeting, and the accounts identified as speakers in that meeting
(`service.team_roster`) — so somebody who has since left the team is still
replaced in the meetings they spoke in, when one is extracted again.
The classifier's placeholders are never stored. Its
answer is a label for each line and, for a line it labels a commitment or a
decision, one line saying what it is; a label carries no text, and a summary has
each `[사람N]` restored to the name it stood for before it is kept, the way the
`llm` reference resolver's sentence is. From either, an answer holding a
placeholder that was never sent is dropped for the text as it was said -- as is
a summary with a number or a name that the lines of its request did not hold.

What still goes out, and is the exposure #392 and #92 ask about:

- names not on the team's roster — people outside the team, somebody who left
  the team and is named in a meeting they did not speak in (or spoke in
  without being identified), an account that was deleted, nicknames, English
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

### NLI (step 4): the same opt-in as the classifier, and the same extra

`AUTUNE_EXTRACTION_NLI_IMPL` accepts `local`, `hosted`, `fake` and `llm`. Step 4
(#12) reads an ambiguous utterance's own text, so an external implementation
is the section 6 question #392 is settling for the classifier. Until
2026-10-05 the classifier's opt-in did not extend to NLI; module B's owner
then asked for step 4 on Gemini for the gate and the demo, and `llm` was added
under the classifier's rules, not looser ones: never the default, refused at
start-up without `AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392=true` and a key, and
for dummy meetings only on a free-tier key.

What `llm` sends is less than the classifier sends about the same meeting: the
masked text of the utterances the classifier called `ambiguous` — a handful —
numbered, with the team's names replaced by `[사람N]`, and one fixed
hypothesis sentence. No speaker, no id, no meeting, no surrounding lines; a
non-consenting speaker's line cannot be among them, because the classifier
never saw it. A meeting's worth usually goes in one request under the
4,000-character cap, to `AUTUNE_EXTRACTION_NLI_MODEL` (default
`gemini-3.8-flash`, no fallback unless `NLI_FALLBACK_MODEL` names one). Its
answer can only promote: `entailment` turns an `ambiguous` row into a
`commitment`; anything else, an unreadable answer included, leaves it for the
confirmation DM. The prompt is unmeasured.

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
entity extractor (`AUTUNE_GAP_NER_IMPL`). Module B is the exception: its
classifier and, since 2026-10-05, its NLI each have an opt-in external `llm`
(`AUTUNE_EXTRACTION_CLASSIFIER_IMPL` and `AUTUNE_EXTRACTION_NLI_IMPL` above),
never the default and pending #392. None of this says anything about `hosted`,
which B does have on both of those settings.

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
and both are started from the repository root. On a laptop
`infra/docker-compose.yml` runs only PostgreSQL and Redis, and no volume is
involved. On the dev server `infra/docker-compose.app.yml` runs both processes in
containers, sharing the tmpfs volume `audio-tmp` (see "The dev server" below).

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

## The dev server

A push to `dev` runs `.github/workflows/dev.yml` on the self-hosted Windows
runner DEV-SERVER1. It runs two compose projects:

- `autune`, from `infra/docker-compose.yml`: PostgreSQL and Redis, the same
  file a laptop uses. The deploy only starts it if it is not running; nothing
  in the application's deploy restarts it.
- `autune-app`, from `infra/docker-compose.app.yml`: `alembic upgrade heads`
  as a one-off, then the containers below, on the `autune` project's network.

**The host has an NVIDIA GeForce RTX 3060 (12 GB)**, on an Intel i5-9400F
with 32 GB of RAM. Docker Desktop hands it to `worker-audio` through WSL 2; that
needs the NVIDIA Windows driver and nothing installed inside WSL. Whisper runs
there on CUDA 12's cuBLAS (module A's `cuda` extra, on `LD_LIBRARY_PATH` in the
image), and pyannote on torch's CUDA 13, which needs driver 580 or later — with
an older driver pyannote alone falls back to the CPU and logs
`diarization_device_unavailable`. Check with
`docker run --rm --gpus all nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi`.

Every long-running container has `restart: unless-stopped`, so all of them come
back after a reboot, provided Docker Desktop itself starts at sign-in.

| Container | Published port | What it runs |
| --- | --- | --- |
| `autune-app-nginx` | 80, on `DEV_PUBLIC_HOST` only | one origin: `/api/` (and the live WebSocket) to `api`, everything else to `web` |
| `autune-app-api` | 8000, on `DEV_PUBLIC_HOST` only | `uvicorn autune_api.main:app`, also reached directly for `/docs` and curl; no password on `/dev` routes here, LAN only |
| `autune-app-worker` | none | Celery, queues `default,cpu_heavy`, `--pool=solo`: B, C, D and E's tasks and every notice |
| `autune-app-worker-audio` | none | Celery, queue `gpu`, `--pool=solo`, with the host's GPU (`gpus: all`) and `AUTUNE_AUDIO_DEVICE=cuda`: module A's tasks only, so a transcription never holds up the other modules |
| `autune-app-beat` | none | Celery beat: sends the `autune.<module>.periodic.*` tasks on their schedules |
| `autune-app-web` | none | `next start`, built with an empty `NEXT_PUBLIC_API_URL`, so the browser calls its own origin |
| `autune-postgres`, `autune-redis` | 5432, 6379, loopback only | as locally |

It runs with `AUTUNE_ENV=local` and the implementations `scripts/up.sh` picks
without `--real-models` (#517), so it holds **dummy meetings only**. Every
`/dev` router is mounted, and `/api/audio/dev/token` issues a token for any
email. nginx therefore puts `/api/*/dev/` behind a password (user `autune`).

**Two ways in, and only one is encrypted end to end.** nginx itself serves
plain HTTP on port 80. The dev password, every bearer token and every uploaded
recording travel over whatever carries that port.

- **On the LAN**, `http://<DEV_PUBLIC_HOST>` is plain HTTP. Acceptable on a
  private network only.
- **Through the domain**, `https://autune.shelldocs.cloud` is TLS on both
  internet legs (checked 2026-10-01):
  - Browser to Cloudflare: Cloudflare's edge certificate; `http://` answers 301
    to `https://`.
  - Cloudflare to host: the DNS record is proxied to the router's public
    address, where a front nginx on the host (not this repository's) terminates
    TLS on 443 with a Cloudflare Origin Certificate (`*.shelldocs.cloud`) and
    answers port 80 only with a 301 to `https://`. Cloudflare's SSL mode is
    therefore Full or Full (strict): under Flexible, Cloudflare would fetch over
    HTTP, get the redirect back, and loop.
  - Front nginx to this stack's nginx: plain HTTP to `DEV_PUBLIC_HOST:80`,
    inside the host's network.

  If the front nginx or its port 80 redirect is ever removed, this no longer
  holds; check again before relying on it. The stack still runs
  `AUTUNE_ENV=local`, so put only dummy data on the server.

Before the first deploy:

- `DEV_PUBLIC_HOST` is a private IP address (LAN or VPN), never a public one
  and never a hostname: compose binds port 80 to it.
- No router forwards 8000. Only nginx (80) and the api (8000) publish a port;
  the api's 8000 bypasses nginx and its password, so it stays LAN-only.

Configuration lives in the repository, not on the host:

| Name | Kind | Required |
| --- | --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | secret | yes; the deploy stops without them |
| `DEV_PUBLIC_HOST` | variable | yes; the IP port 80 is bound to |
| `AUTUNE_SECRET_KEY` | secret | yes; signs every session. `AUTUNE_ENV=local` turns off the check that refuses `.env.example`'s public key, so without this anyone could forge a token. Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DEV_BASIC_AUTH_PASSWORD` | secret | yes; the password for `/api/*/dev/` |
| `AUTUNE_AUDIO_HF_TOKEN` | secret | yes; pyannote is gated, so every upload fails after transcription without it ("Pyannote and its three gated repositories" above) |
| `AUTUNE_AGENT_LLM_API_KEY` | secret | no; without it the agent chat refuses |
| `AUTUNE_LLM_API_KEY` | secret | yes; the provider key under its shared name. Module B reads it when `AUTUNE_EXTRACTION_LLM_API_KEY` is blank (#701). The compose file sets `AUTUNE_EXTRACTION_CLASSIFIER_IMPL=llm` with `AUTUNE_EXTRACTION_LLM_MODEL=gemini-3.5-flash-lite`, a blank `AUTUNE_EXTRACTION_LLM_FALLBACK_MODEL` and `AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392=true` (the user's decision of 2026-10-05, #392), so **with this key every consenting speaker's masked utterance of a meeting on this server goes to the provider.** It also sets `AUTUNE_EXTRACTION_RESOLVER_IMPL=llm` (the user's decision of 2026-10-02): for each commitment and decision, that utterance and its neighbours go a second time, to the resolver's own models (`resolver_model`, and `resolver_second_model` when the first answer fails a check), the team roster's names replaced (#530). A real meeting needs a paid key; nothing in the code can tell which tier a key is. It also sets `AUTUNE_EXTRACTION_TITLE_IMPL=llm` (module B's owner, 2026-10-09): after an extraction, the sentences it wrote for that meeting's action items and decisions go once more, in one request, for a title of twenty characters each -- never a sentence a person typed or edited, nor a row whose speech was deleted. "Check configuration" refuses a deploy without it. Both NLI implementations call no provider here. The agent, C and D do not read this name |
| `AUTUNE_GOOGLE_CLIENT_ID`, `AUTUNE_GOOGLE_CLIENT_SECRET` | secret | no; without them Google sign-in answers "not configured" and the dev token still works. The OAuth client must list `https://autune.shelldocs.cloud/api/auth/google/callback` as an authorised redirect URI. Sign-in creates a user for any verified Google account, and nginx asks for no password before `/api/auth/google/start` (#890). **The one thing that keeps an account outside the team from signing in on the public domain is Google's OAuth consent screen: it must stay in Testing, with only the team as test users (#602).** That is a setting in the Google Cloud console of the project this client belongs to, and no code here checks it: publish the app, or add an account to the list, and that account signs in and becomes a user. Reopen #890 before changing the publishing status or the test-user list. The password on `/api/*/dev/` stays |
| `AUTUNE_GOOGLE_INTEGRATION_CLIENT_ID`, `AUTUNE_GOOGLE_INTEGRATION_CLIENT_SECRET` | secret | no; a second Google client for what a person connects (their calendar), apart from sign-in. Without them the sign-in client does both. Both or neither — the deploy stops on half. It uses the same callback, so `https://autune.shelldocs.cloud/api/auth/google/callback` must be an authorised redirect URI on this client too; if it lives in another Google Cloud project, that project's consent screen needs the same Testing state and test users |
| `AUTUNE_SLACK_CLIENT_ID`, `AUTUNE_SLACK_CLIENT_SECRET` | secret | no; without them "Add to Slack" answers "not configured". Redirect URL to register in the Slack app: `https://autune.shelldocs.cloud/api/auth/slack/callback` The compose file sets `AUTUNE_EXTRACTION_DAILY_DIGEST=true` and `AUTUNE_EXTRACTION_WEEKLY_DIGEST=true` (the user's decision of 2026-10-05), so **with Slack connected this site sends DMs to real Slack accounts**: a morning DM Tuesday to Friday (09:00-12:00 KST) and Monday's digest, to each person about their own open, confirmed action items, where the team connected Slack and the person linked their account. A person stops both with "마감 알림 받기" off or their own leave dates. With a digest on, the worker also reads Google's public holiday calendar twice a day (no credentials, nothing sent). Due-date reminders and the out-of-office read stay off |
| `AUTUNE_NOTION_CLIENT_ID`, `AUTUNE_NOTION_CLIENT_SECRET` | secret | no; the Notion public integration. Redirect URI to register: `https://autune.shelldocs.cloud/api/auth/notion/callback` |
| `AUTUNE_JIRA_CLIENT_ID`, `AUTUNE_JIRA_CLIENT_SECRET` | secret | no; the Atlassian OAuth 2.0 (3LO) app. Callback URL to register: `https://autune.shelldocs.cloud/api/auth/jira/callback` |
| `AUTUNE_ENCRYPTION_KEY` | secret | yes as soon as any Slack, Notion, Jira or Google integration value above is set: the deploy stops without it. It encrypts every stored connection, and `AUTUNE_ENV=local` turns off the start-up check that would otherwise require it. A calendar connected through the sign-in client needs it as well. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. The deploy also checks that a key that is set is a valid one. **Changing it strands every stored connection** — each has to be connected again |

The redirect URIs are not configuration here. `infra/docker-compose.app.yml`
builds each from `DEV_SITE_URL` (`<site>/api/auth/<service>/callback`), because
the browser must come back to the origin its state cookie was set on. A
`*_REDIRECT_URI` secret is read by nothing.

The compose file also sets `AUTUNE_EXTRACTION_MATERIAL_UPLOAD=true` with
`AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_IMPL=local` (module B's owner, 2026-10-12,
#817), so **this site keeps the masked text of documents uploaded to a team's
자료**, and a member of the team finds one by typing a question. The file itself
is not kept. An upload is deleted at the end of its team's retention window
unless the team chose to keep its uploads until a member deletes them, and a
team nobody is on loses them at an hourly check, whatever it chose
(`docs/architecture/privacy.md`, section 4). The two are set together:
with uploads on and the embedder at
`.env.example`'s `fake`, the api, the workers and beat do not start. `local` is
KURE-v1 inside the `api` container, on the CPU, read from the `models` volume:
no material text and no question leaves the server. The container starts
loading it when it starts, in the background, and holds it from then on
(about 2 GB, as measured on a laptop); an upload or search sent before the
load ends waits for it. Two things do leave. A file refused for a
confidentiality marking is told to the team's approvers with the '전체' scope
in one Slack DM -- a count and a link, nothing of the file -- where the team
connected Slack and the approver linked their account. And each time the `api`
container loads the model, so at every start of it, it asks huggingface.co
about the model's files -- requests that name the model and its revision,
nothing of a material, a question or a team -- and downloads the weights from
there when the `models` volume does not hold them yet.
`AUTUNE_EXTRACTION_MATERIAL_SEARCH_TOOL` stays off, so the assistant's model
is given no material text.

To sign in, open the site and use Google with an account on the consent
screen's test-user list; nothing else is asked. An account that is not on the
list is refused by Google before it reaches Autune.

To act without a Google account, or as several users, get a token with the
password instead, then set it in the browser console on the site
(`localStorage.setItem("autune.token", "<token>")`):

```bash
curl -s -u autune:<password> -X POST http://<DEV_PUBLIC_HOST>/api/audio/dev/token \
  -H 'content-type: application/json' -d '{"email": "you@example.com"}'
```

The first upload downloads Whisper and the first context task KURE-v1 and its
re-ranker (several GB) into the `models` volume, which survives redeploys.

## The release host

A push to `infra/release-1014` runs `.github/workflows/release-aws.yml` on a
GitHub-hosted runner. It deploys to one EC2 instance in ap-northeast-2 with no
SSH, no deploy key and no long-lived AWS key:

1. The runner assumes the AWS role in the repository variable
   `AWS_DEPLOY_ROLE_ARN` through OIDC. The role's trust policy accepts only
   this repository's `infra/release-1014` branch. The repository sends an
   immutable subject, `repo:<owner>@<owner id>/<repo>@<repo id>:ref:refs/heads/infra/release-1014`
   (read the prefix with
   `gh api repos/<owner>/<repo>/actions/oidc/customization/sub`). A trust
   policy written with the repository's name alone does not match it, and the
   job stops at "Assume the deploy role" with `Not authorized to perform
   sts:AssumeRoleWithWebIdentity`.
2. It sends one SSM Run Command to the instance in `AWS_DEPLOY_INSTANCE_ID`.
   The command fetches the pushed commit with the job's `GITHUB_TOKEN`, passed
   as a request header and not stored in `.git/config`, and runs
   `infra/aws/deploy.sh`.
3. `deploy.sh` reads two Secrets Manager secrets with the instance role:
   `autune/db` (RDS and ElastiCache endpoints and the database login) and
   `autune/app` (one entry per environment variable: the secret key, the
   encryption key, the LLM and Hugging Face keys, the OAuth client ids and
   `AUTUNE_SITE_URL`, the address people reach the site on). It
   builds the images, migrates, checks the encryption key and starts the
   stack. No secret value is printed.

The stack is `infra/docker-compose.app.yml` with
`infra/docker-compose.release.yml` on top, as project `autune-app`:

| What | Release host |
| --- | --- |
| `AUTUNE_ENV` | `production`: no `/dev` router, Secure cookies, and settings refuse the shipped secret key and a missing encryption key |
| Database, broker | RDS PostgreSQL 16 (TLS required, `sslmode=require`) and ElastiCache Redis, not containers; there is no `postgres` or `redis` service |
| `worker-audio` | CPU (`AUTUNE_AUDIO_DEVICE=cpu`, int8), no `gpus` entry. Expect transcription to run near real time, not faster |
| Published port | nginx on 80 only, behind an ALB that terminates TLS for the address in `AUTUNE_SITE_URL` |
| Raw audio | the same `audio-tmp` tmpfs as everywhere; swap is off on the host, so a page of it cannot reach the disk (`privacy.md` section 1) |
| Switches | exactly those of `docker-compose.app.yml` on the release branch; this file turns nothing on. On this branch `AUTUNE_EXTRACTION_MATERIAL_UPLOAD` and `AUTUNE_EXTRACTION_MATERIAL_SEARCH_TOOL` are `true` (#817), with the embedder `local` in the `api` container (about 2 GB of the host's 32 GiB; the weights are downloaded from huggingface.co at the first start) |

Sign-in is Google only here: the `/api/audio/dev/token` route is not mounted
under `production`, and the nginx password block has nothing behind it.

**The site's address is `app.autune.cloud`** (decided on #1343). `deploy.sh`
has no default for `AUTUNE_SITE_URL` and stops without it. It must not be
`autune.cloud` itself, which is the public landing page (`apps/landing`,
#1338) and has to show that page at the address registered with the ad
network, nor a path under it. The landing page's owner agreed to the name
`app.autune.cloud` on two conditions that live in the code, not in this
deployment, and that must be met before an ad script goes on the landing
page: the session cookie takes the `__Host-` prefix, a state-changing request
and the live WebSocket handshake are refused when their `Origin` is not the
app's own (the Slack events route, which Slack posts without an `Origin`, is
left out), and the OAuth state cookie takes `__Host-` too, with `Path=/`. Both
are in the code (#1346) and have been deployed here since 2026-10-15. The two
names share a site, so no third-party script goes on the app's own origin. The
Meta Pixel is in the landing page's source (#1358); whether it is live depends
on the landing build being uploaded, which is outside this repository.

**Anyone with a verified Google account can sign in here.** There is no allow
list: `upsert_user_from_google` creates a user for any Google identity that
comes back, the OAuth client in `autune/app` is the main project's and its
consent screen is published (#1320), so Google refuses nobody. What decides who
reaches the sign-in page at all is the load balancer's security group
(`autune-alb-sg`): ports 80 and 443. That setting lives outside this
repository and nothing here checks it; whoever changes it changes who can sign
in.

**The door was opened on 2026-10-15 at 13:24 KST**, at the project owner's
word, by adding `0.0.0.0/0` on ports 80 and 443 to `autune-alb-sg`. The
decision issue, #1349, was still open then: of the four module owners only one
had answered, with conditions. This section records what stood where, so that
a reader does not take the door for something the owners agreed to in full.

| Condition | State when the door was opened |
| --- | --- |
| `__Host-` cookies and the `Origin` check (#1346), with the nginx `Host` fix (#1351) | on this branch and deployed (run on `2bc4b189`) |
| Privacy policy names AWS as the processor, the consent version says so | deployed (#1347 reached this branch through #1352 and #1359) |
| Notice period of article 15 (2) | not waited for; skipped by agreement of the four owners on #1347 |
| EC2 instance security group takes port 80 only from `autune-alb-sg` | read and true |
| Sign-in and the live WebSocket exercised on the host | the WebSocket handshake was exercised (101 with the app's `Origin`, 403 with another); a signed-in POST was **not** |
| A usage limit or alert on the LLM key (asked for on #1349) | **not met**: none is configured, and the decision is on hold |
| #890 reopened, and a separate Testing client or an allow list in code (#1342) | **not settled**: neither was done, and #1349 does not say they are dropped |

Until #1349 is answered by the other owners, treat the last two rows as open
debts, not as decided.

While the door is open, two things follow that were not true of the team
trial. Slack's servers can reach `/api/slack/events`, so connecting Slack
works. And there is **no per-user or per-team quota** on transcription or on
the LLM: the limits in code are a 500 MB cap per file and the 429s of a few
single features. Anyone who signs up can use the CPU and the paid LLM key. The
only way to refuse someone is to narrow the security group again.

The broker connection is encrypted: `deploy.sh` builds `rediss://` with
`ssl_cert_reqs=required`, because Celery payloads carry masked transcript
lines and a VPC is not a reason to send them in the clear. The matching
setting lives outside this repository: the ElastiCache replication group must
have in-transit encryption on (`preferred` while the first deploy is checked,
then `required`). Without it the connection is refused and the deploy fails at
`up --wait`, which is the visible way for it to go wrong.

`redis_host` in `autune/db` is the replication group's primary endpoint,
`master.<group>.<id>.<region>.cache.amazonaws.com`. The certificate is issued
for the group's names, so the node-group name that some console views show
(`<group>.<id>.ng.0001.<region>.cache.amazonaws.com`) fails the hostname check.
That mistake is quiet: the stack starts and every health check passes, because
none of them touches the broker, and the first thing to fail is the OAuth state
store, so "Continue with Google" returns an Internal Server Error with
`CERTIFICATE_VERIFY_FAILED: Hostname mismatch` in the `api` log. The check
stays on; `ssl_cert_reqs=none` is not the fix.

To stop paying for the host overnight, stop the EC2 instance and the RDS
instance (the broker and the load balancer cannot be stopped; delete them if
the site is idle for more than a day or two). RDS starts itself again after
seven days. Everything in the `autune-app` project comes back with the host
(`restart: unless-stopped`).

### Opening hours

The host is planned to be open on weekdays from 09:00 to 18:00 KST and not
at all on weekends (the owner of module B, 2026-10-16). It is started and
stopped by hand, as in the paragraph above; nothing schedules it.

- **Start about 08:40.** The EC2 instance and the RDS instance are started
  together and the stack comes back with the host, but RDS takes about five
  minutes to be available. Check `/` and the five `/api/*/health` paths after
  that, as after a deploy.
- **A weekend is within the seven days.** RDS starts itself again after seven
  days stopped; from Friday 18:00 to Monday morning is about 62 hours.
- **Land release merges inside the hours.** A push to `infra/release-1014`
  runs `release-aws.yml`, which deploys through SSM, and SSM cannot run a
  command on a stopped instance, so a push while the host is stopped leaves a
  red run (not tried here). Do not merge to the branch on a Friday after 18:00
  or on a weekend; start the host first, or wait for Monday.
- **Outside the hours the app does not answer.** The load balancer has no
  healthy target (a 503 is what to expect; not checked here). The sign-in
  page and the live transcripts are unavailable until the next start. Stored
  data stays in the stopped database, but a recording still being transcribed
  at the stop is gone: its temp file lives in memory.
- **What it costs.** The two stopped resources are the large part of the
  bill; the load balancer, the broker, the public addresses and the disks stay
  at about $2 a day. At 45 hours a week that is about $180 a month, against
  about $500 left on. Both numbers are estimates from public unit prices: the
  account's billing could not be read with the deploy user (Cost Explorer is
  denied to it), and the language-model calls are not in them.

### Rolling back

The workflow deploys the commit that was pushed to `infra/release-1014`
(`github.sha`), and the deploy role's trust policy accepts only that branch,
so an older commit cannot be deployed from anywhere else. To go back, merge a
revert of the change into the branch; the push deploys it. The workflow also
has `workflow_dispatch` and deploys `github.sha`; re-running an earlier run
from the Actions tab should deploy that run's commit again, faster than a
revert pull request, but it leaves the branch tip unchanged, so the next push
deploys the tip again. That route has not been tried here.

- **Time.** `deploy.sh` rebuilds the images every time and prunes the one it
  replaced, so a rollback is a rebuild: a few minutes when the build cache is
  warm, then `up --wait` (at most 900 seconds). A failed `up` leaves the job
  red and prints `ps -a` in the log; the previous containers are already gone.
- **Schema.** Migrations run before the containers start and the script never
  runs a downgrade. A revert of code does not revert the schema, so a
  migration that is not additive is not safe to roll back past.
- **Closing the door.** Without a deploy, in minutes, replace the
  `0.0.0.0/0` rules on ports 80 and 443 of `autune-alb-sg` with the team's
  addresses. This closes the door only while the instance itself takes port 80
  from `autune-alb-sg` alone (or has no public address); check that first, or
  narrowing the load balancer's rules does not stop anyone:

  ```bash
  sg=$(aws ec2 describe-security-groups --filters Name=group-name,Values=autune-alb-sg \
    --query 'SecurityGroups[0].GroupId' --output text)
  for port in 80 443; do
    aws ec2 revoke-security-group-ingress --group-id "$sg" --protocol tcp \
      --port "$port" --cidr 0.0.0.0/0
    aws ec2 authorize-security-group-ingress --group-id "$sg" --protocol tcp \
      --port "$port" --cidr <team address>/32
  done
  ```

  If a `::/0` rule was added too, revoke it the same way. New connections
  from other addresses are refused at once; one that is already established
  keeps working until it closes. The stack keeps running.
- **After a rollback or a start**, look at the same things as after a
  deploy: `/` answers 200, `/api/auth/google/start` redirects to Google and sets a `__Host-`
  state cookie, a POST naming another host in `Origin` is refused with 403,
  and one member can sign in, open a live transcript and upload a file.

To stop the host, see the paragraph above.

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
