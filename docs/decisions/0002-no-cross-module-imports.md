# 0002. Modules communicate only through contracts and events

**Status:** Accepted
**Date:** 2026-09-08
**Deciders:** the whole team

## Context

The folder structure from ADR 0001 only helps if the boundaries are real. A
single `from autune_audio.schemas import Transcript` in module C compiles,
passes C's tests, and quietly recreates every coupling the structure was meant
to prevent.

The failure is delayed and asymmetric: the person who wrote the import sees no
problem, and the person whose code breaks is someone else, weeks later.

## Decision

The five modules never import one another. They communicate through exactly two
channels:

1. **Contract types** — Pydantic models in `packages/contracts`, frozen after W1
   and additive-only afterwards.
2. **Celery events** — payloads that are always serialized contract types.

Reading another module's database tables is equally forbidden.

Enforced in CI by `import-linter` with an `independence` contract. A failure is
fixed by removing the import, never by editing the linter configuration.

## Alternatives considered

**Convention without enforcement.** "We agreed not to." Rejected: this rule is
violated under deadline pressure by well-intentioned people, and the violation
is invisible until it hurts.

**Shared service layer that all modules call.** A `services/` package holding
cross-module logic. Rejected: it becomes the file everyone edits — the exact
contention ADR 0001 removes — and it grows into a hidden coupling point.

**Direct HTTP calls between modules.** Real isolation, network boundary.
Rejected: synchronous coupling means one slow module blocks another, and the
pipeline is already asynchronous. Events give isolation without the latency
chain.

**Shared database access with a read-only convention.** Simplest to implement.
Rejected: a schema change in one module then silently breaks another's queries,
which is precisely the delayed failure we are avoiding.

## Consequences

**Easier:** a module can be refactored, redeployed, or rewritten without
coordinating. Model loading stays isolated, so `apps/api` does not import
Whisper, DeBERTa, spaCy, SBERT, and XGBoost at startup. Migrations stay
independent.

**Harder:** getting data from another module requires a contract change, which
requires coordination. Some logic is duplicated across modules.

**Accepted cost:** contract changes are slow by design. `packages/contracts` is
the interface four modules depend on; it should be hard to change.
