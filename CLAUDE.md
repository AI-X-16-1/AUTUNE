# CLAUDE.md — Autune

Read this file before touching anything in this repository. It contains only the
rules that break other people's code (or the law) when violated. Everything else
lives in `docs/` and is linked from here.

---

## 0. Language Policy — applies to every team member and every AI agent

| Surface | Language |
| --- | --- |
| Everything written into the repository — docs, code comments, docstrings, commit messages, PR titles and descriptions, ADRs, test names, log messages, error strings | **English** |
| Conversation with the user (chat, review comments addressed to a person, Slack) | **Korean (한국어)** |

Rationale: the repository is the shared, permanent artifact and must be readable
by any tool or contributor; conversation is ephemeral and should be in the
team's working language. This is a team rule every contributor, human or AI,
follows.

**The one exception: `/README.md` is written in Korean.** It is the front door
for Korean readers arriving at the project — it explains what Autune is, not how
to build it. Do not translate it to English. Everything under `docs/`, every
`CLAUDE.md`, and everything inside the code stays English.

If asked to write a document, write it in English. If asked a question, answer
in Korean.

---

## 1. What Autune is

Autune (Auto + Attune) is a meeting intelligence platform. A team uploads a
meeting recording; Autune transcribes it with speaker attribution, masks
personal data, extracts action items and decisions, detects what was *not*
discussed, links the meeting to past meetings, and reports patterns over time.

The product is built by five people over six weeks, one module each. The
repository structure exists to let those five people work in parallel without
touching the same files.

Full product context: `docs/product/prd.md`. Domain vocabulary:
`docs/product/glossary.md`.

---

## 2. Ownership map

| Module | Package | Owner | Backend path | Frontend path | Table prefix |
| --- | --- | --- | --- | --- | --- |
| A. Audio Pipeline | `autune_audio` | 김민경 | `modules/audio/` | `apps/web/src/features/transcript/` | `aud_` |
| B. Structured Extraction | `autune_extraction` | 강민구 | `modules/extraction/` | `apps/web/src/features/actions/` | `ext_` |
| C. Gap Detection | `autune_gap` | 박재경 | `modules/gap/` | `apps/web/src/features/gap/` | `gap_` |
| D. Meeting Context Engine | `autune_context` | 문민재 | `modules/context/` | `apps/web/src/features/context/` | `ctx_` |
| E. Meeting Intelligence | `autune_intelligence` | 이승환 | `modules/intelligence/` | `apps/web/src/features/dashboard/` | `intel_` |

Shared, owned by the whole team (changes require broad approval):
`packages/contracts`, `packages/core`, `packages/integrations`, `apps/*`,
`infra/`.

Each module has its own `CLAUDE.md` (for example `modules/gap/CLAUDE.md`). When
working inside a module, read that file too.

---

## 3. The 11 Invariants

Violating any of these breaks someone else's work, the build, or the law.

### 1. English in the repo, Korean in conversation
See section 0. No exceptions.

### 2. Modules never import each other
`autune_audio`, `autune_extraction`, `autune_gap`, `autune_context`, and
`autune_intelligence` must not import one another — not directly, not through a
helper, not inside a function body. They may import `autune_contracts`,
`autune_core`, and `autune_integrations`.

Cross-module communication happens through exactly two channels: **contract
types** in `packages/contracts` and **Celery events**. Enforced in CI by
import-linter. → `docs/architecture/module-boundaries.md`

### 3. Module tables carry a module prefix
Every table a module owns is named `<prefix>_<name>`: `aud_segments`,
`ext_action_items`, `gap_topics`, `ctx_decision_versions`, `intel_scores`. A
table without a prefix is a shared entity and does not belong to a module.
→ `docs/architecture/data-model.md`

### 4. Shared entities live in `packages/core`; only A writes them
`User`, `Team`, `Meeting`, `Participant`, `Utterance` are defined in
`packages/core`. Module A writes them. B, C, D, E read them and never issue
`INSERT`, `UPDATE`, or `DELETE` against them. Store your own derived state in
your own prefixed tables. → `docs/architecture/data-model.md`

### 5. `packages/contracts` is frozen after W1 — additive changes only
Adding an optional field is fine. Removing a field, renaming a field, changing
a type, or tightening a constraint is a breaking change: announce it in Slack,
get approval from every affected module owner, and bump the contract version in
the same PR. A silent contract change breaks four modules at once.
→ `docs/architecture/contracts.md`

### 6. `apps/` is assembly only — no business logic, no hardcoded registration
`apps/api`, `apps/worker`, and `apps/bot` wire modules together and nothing
else. Routers and Celery tasks are discovered by iterating over the module list;
never append your module by hand to a registration block. If you find yourself
editing a file under `apps/` to ship a feature, the feature belongs in your
module. → `docs/architecture/monorepo.md`

### 7. Each module owns an independent Alembic branch
Your first revision sets `down_revision = None` and
`branch_labels = ("<module>",)`. Every later revision chains only onto your own
module's revisions. Apply with `alembic upgrade heads` (plural). Never edit
another module's migration file. → `docs/engineering/migrations.md`

### 8. Dependencies go in your own module's manifest
Add Python packages to `modules/<name>/pyproject.toml`, never to the root
`pyproject.toml`. Add JS packages to the workspace package that uses them. On a
lockfile conflict, do not hand-merge — regenerate with `uv lock` or
`pnpm install`. → `docs/engineering/dependencies.md`

### 9. Branch `<module>/<task>`, PR only
Branch names look like `gap/topic-graph` or `audio/pii-masking`. Direct pushes
to `main` are forbidden and a pre-push hook refuses them — run
`git config core.hooksPath .githooks` once per clone. Every change lands through
a pull request: one approval for your own module, the owner's approval for
anyone else's.
→ `docs/engineering/workflow.md`

### 10. Do not edit files you do not own
CODEOWNERS is the source of truth. If a change you need lives in someone else's
module, in `packages/contracts`, or under `apps/`, open an issue or ask the
owner in Slack. Do not "just fix it" across a boundary.

### 11. Privacy rules are code-level constraints, not policy documents
- Raw audio is deleted immediately after transcription completes. It is never
  persisted to durable storage, never logged, never copied into a temp path that
  survives the task.
- Transcript text is PII-masked **before** it is written to the database. The
  unmasked string must not reach any store, log line, exception message, or
  external integration.
- Speaking-ratio data is delivered only to the speaker themselves. No endpoint,
  query, dashboard, or export may return one person's speaking ratio to anyone
  else — including team admins. Aggregate speaking-ratio records are not stored.
- Analysis results have a retention window (90 days by default) and users can
  delete their own data at any time.

→ `docs/architecture/privacy.md`

---

## 4. Before you code — where to look

| Task | Read first |
| --- | --- |
| Adding an API endpoint | `docs/architecture/module-boundaries.md`, `docs/engineering/conventions.md` |
| Adding a database table or column | `docs/architecture/data-model.md`, `docs/engineering/migrations.md` |
| Sending data to or receiving data from another module | `docs/architecture/contracts.md`, `docs/architecture/async-pipeline.md` |
| Anything touching transcripts, audio, or speaker identity | `docs/architecture/privacy.md` |
| Adding a Celery task | `docs/architecture/async-pipeline.md` |
| Adding a dependency | `docs/engineering/dependencies.md` |
| Frontend work | `docs/engineering/conventions.md` (frontend features mirror backend modules 1:1) |
| Calling Slack, Notion, Jira, or Google Calendar | `packages/integrations`, `docs/engineering/environments.md` |
| Writing or running tests | `docs/engineering/testing.md` |
| Local setup, Docker, environment variables | `docs/engineering/environments.md` |
| Understanding why the repo is shaped this way | `docs/decisions/` |

Start here if you are new: `docs/README.md`.

---

## 5. Runtime and tooling

- Python **3.12**, managed by **uv** workspace
- Node **22** (current LTS), managed by **pnpm** workspace
- FastAPI, Celery + Redis, PostgreSQL (with pgvector), Neo4j
- Next.js + Tailwind
- Lint/format: **ruff** (Python), **eslint** + **prettier** (JS/TS)
- Types: **mypy** (Python), **tsc** (TS)
- Boundaries: **import-linter**

---

## 6. Working agreements for AI agents

- Stay inside the module you were asked to work in. If the task requires a
  change outside it, say so and stop rather than making the change.
- Never invent a contract field. If the data you need is not in
  `packages/contracts`, that is a design conversation, not an edit.
- Never weaken a privacy constraint to make a test pass or a feature work.
- When a doc and the code disagree, the doc is probably stale — flag it, do not
  silently follow either one.
- Prefer adding to your module over generalizing into `packages/`. Shared code
  is expensive: it needs team approval and it breaks five people when wrong.
