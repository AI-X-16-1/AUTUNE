# Autune Documentation

All documents in this repository are written in **English**. Conversation with
teammates and users happens in **Korean**. See `CLAUDE.md` section 0.

The single exception is `/README.md`, the project's front door, written in
Korean for readers arriving at the repository. It covers what Autune is; these
documents cover how it is built.

## Reading order

**New to the project (30 minutes):**
1. `/CLAUDE.md` — the rules, the ownership map, the invariants
2. `product/prd.md` — what we are building and why
3. `product/glossary.md` — the vocabulary used everywhere else
4. `architecture/monorepo.md` — how the repository is shaped
5. `engineering/environments.md` — get it running locally

**Before your first pull request:**
- `engineering/workflow.md` — branches, PRs, reviews, commits
- `engineering/conventions.md` — naming and code layout
- `modules/<your module>.md` — your module's contract with the rest of the system

**Before writing any frontend code:**
- `design/ui-spec.md` — screen inventory, shared components, state mapping
- `design/design-tokens.json` — the only colors, sizes and motion values permitted

## Index

### product/
| Document | Contents |
| --- | --- |
| `prd.md` | Problem, users, module responsibilities, AI stack, KPIs, roadmap, MVP scope |
| `glossary.md` | Domain terms — utterance, action item, gap, decision lineage, and the rest |

### architecture/
| Document | Contents |
| --- | --- |
| `monorepo.md` | The three-layer structure (`packages/`, `modules/`, `apps/`) and why |
| `module-boundaries.md` | The no-cross-import rule and how CI enforces it |
| `contracts.md` | The five inter-module payloads and the change policy |
| `data-model.md` | Shared entities, per-module tables, prefix rules, datastore split |
| `async-pipeline.md` | Celery event flow A → B/C/D → E, retries, idempotency |
| `privacy.md` | Raw-audio deletion, PII masking, speaking-ratio confidentiality, retention |
| `integrations.md` | Slack, Notion, Jira, Calendar wrappers and the outbound privacy boundary |

### engineering/
| Document | Contents |
| --- | --- |
| `workflow.md` | Branching, PRs, CODEOWNERS, commit format, language rule |
| `conventions.md` | Naming, module file layout, API shape, errors, logging, frontend layout |
| `migrations.md` | Alembic branch-per-module runbook |
| `dependencies.md` | uv and pnpm workspaces, lockfile conflict policy |
| `testing.md` | Test layers, fixtures, model-dependent tests, CI gates |
| `environments.md` | Docker services, environment variables, local setup, secrets |
| `external-approvals.md` | HuggingFace, Slack, Notion registrations and the AI Hub terms questions |

### design/
| Document | Contents |
| --- | --- |
| `ui-spec.md` | Rules at a glance, all 33 screens (S01–S33), shared components, data and state mapping, UI-visible privacy constraints |
| `design-tokens.json` | Color, typography, spacing, layout, shape, control, border and motion tokens, light and dark |
| `AUTUNE Spec 00~04 *.dc.html` | Design source. Spec 00 is the design system; 01–04 are the screens |
| `AUTUNE 실시간 전사.dc.html` | S13 live transcript, drawn at full size (1440×936) |
| `support.js` | Generated design-canvas runtime the `.dc.html` files load. Do not edit it — it is rebuilt from `dc-runtime` |

Open a `.dc.html` file directly in a browser to read it. The artboards are plain
HTML and CSS, so they render without any network access; the Pretendard webfont
(jsdelivr) and the React runtime that `support.js` fetches lazily (unpkg) are
only needed for correct type and for interactive canvas behavior.

The `.dc.html` files are design deliverables, not documents: their UI copy is
Korean because user-facing copy is Korean. Do not translate them. `support.js`
must stay in this folder — every file loads it as `./support.js` — and the
filenames must stay identical, because the specs cross-link each other by name.

### modules/
One document per module: scope, owner, inputs and outputs, tables, endpoints,
Celery tasks, AI stack, and explicit non-goals.

`_template.md` is the template for a new module document.

### decisions/
Architecture Decision Records. Read `decisions/README.md` for the process.

## Keeping documents accurate

A stale document is worse than no document. When you change behavior that a
document describes, update the document in the same pull request. Reviewers
should reject a PR that invalidates a document without updating it.
