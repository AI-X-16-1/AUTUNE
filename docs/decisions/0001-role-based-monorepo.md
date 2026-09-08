# 0001. Role-based monorepo instead of frontend/backend

**Status:** Accepted
**Date:** 2026-09-08
**Deciders:** the whole team

## Context

Five people build five modules full-stack in six weeks. Nobody is dedicated to
integration, and nobody has time to resolve merge conflicts every day.

The conventional split — `frontend/` and `backend/` — puts all five people in
the same files. Every module needs a model, a router, a task, and a page; in a
`backend/app/` layout those live in `models.py`, `main.py`, `tasks.py`, and a
shared page directory. Five people editing four files for six weeks is a
guaranteed conflict on the days when everyone is moving fastest.

## Decision

Three layers, split by role rather than by technology:

| Layer | Folder | Ownership |
| --- | --- | --- |
| Shared | `packages/` | Team consensus, changes rarely |
| Owned | `modules/` | One owner per module |
| Assembly | `apps/` | Shared, almost never edited |

The frontend is cut along the same axis: `apps/web/src/features/<module>/`, so
one person owns `modules/gap/` and `features/gap/` together.

To keep `apps/` genuinely untouched, router and task registration iterate over
a module list rather than being hardcoded, and each module owns an independent
Alembic branch.

## Alternatives considered

**`frontend/` + `backend/`.** Familiar, and the default for most tutorials.
Rejected: it maximizes shared-file contention exactly where the team has the
least slack.

**Separate repositories per module.** Real isolation. Rejected: five
repositories plus a contracts repository means version skew, cross-repo PRs, and
six CI pipelines — in a six-week project that overhead exceeds the conflicts it
avoids.

**Single application, feature folders, no packages.** Simpler than a workspace.
Rejected: with no dependency boundary, module A's torch and whisper install
everywhere and every deploy carries every model.

## Consequences

**Easier:** five people work in parallel with almost no shared-file contention.
Ownership is unambiguous — the folder says who to ask. A module can be
dependency-isolated, so C does not install A's several gigabytes of ML wheels.

**Harder:** shared code costs more to change, deliberately. Adding a field
consumed by four modules requires coordination. The workspace tooling (uv,
pnpm, import-linter, multi-branch Alembic) is more setup than a single app.

**Accepted cost:** some duplication across modules. Two modules writing similar
date-parsing helpers is cheaper than the coupling that sharing them would
create.
