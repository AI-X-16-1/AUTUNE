# 0008. Module E's `intel_` tables: natural keys, and `intel_reports` deletion is deferred

**Status:** Accepted
**Date:** 2026-09-09
**Deciders:** 이승환 (module E owner). The deferred deletion path needs `packages/core` and `.github` owners.

## Context

Module E aggregates B, C and D across meetings. `../modules/intelligence.md`
names the six tables it owns — `intel_completion`, `intel_scores`,
`intel_gap_patterns`, `intel_alignment`, `intel_predictions`, `intel_reports`.
The module doc leaves two schema questions open, and one privacy constraint
could not be fully met in the first cut.

`../architecture/privacy.md` §4 requires every module-owned table to be
reachable for deletion from a `meeting_id` or a `user_id`.

## Decision

**1. Primary keys are natural or composite, not prefixed ids.**
`autune_core.ids` generates prefixed string ids and states that modules do not
add their own prefixes. Rather than change that shared file, each table uses a
natural key: `meeting_id` for `intel_completion` and `intel_scores`;
`(meeting_id, pattern_type)`, `(meeting_id, role_a, role_b)` and
`(meeting_id, kind, horizon_days)` for the append-per-meeting tables;
`(team_id, period_start)` for `intel_reports`. These keys also encode a real
invariant — one row per role pair / pattern / prediction kind per meeting — so
re-aggregating a meeting is an upsert, not an append.

**2. The revision depends on `core`'s `shared_entities` revision.**
`modules/intelligence/migrations/…_add_intel_tables.py` sets
`depends_on = "d34994600a9a"`. The `intel_` tables have foreign keys to
`meetings` and `teams`; `alembic upgrade heads` does not otherwise order the
`core` and `intelligence` branches. Any module with a foreign key to a shared
entity needs the same pin.

**3. `intel_reports` has no per-meeting deletion path in this cut.**
Five of the six tables reach deletion by `ON DELETE CASCADE` from `meetings.id`.
`intel_reports` is scoped to a team and a week, not a meeting, so it cascades
from `teams.id` and is otherwise removed by the 90-day retention sweep. It
carries a `source_meeting_ids` column so a per-meeting hook can be added later
without a schema change.

## Alternatives considered

**Add `INTEL_*` prefixes to `autune_core.ids`.** Rejected: a change to a shared,
team-owned file for no functional gain. The natural keys are sufficient and more
descriptive.

**Register an `autune_core.deletion` meeting-hook for `intel_reports`** — the
original plan. Built, then removed. The hook registers into a process-global
registry at import time. `packages/core/tests/test_core.py::test_deletion_hooks_run_for_registered_modules`
then calls `run_meeting_hooks`, which iterates every registered hook — so a core
unit test ran module E's real `DELETE FROM intel_reports` and failed on the
empty database CI uses (CI runs the test step before migrations). It passed only
on an already-migrated developer database. Making the hook safe needs three
shared-owner changes: test-isolation of the registry in
`packages/core/tests/test_core.py`, running the CI `Migrations` step before
`Tests`, and `apps/` discovery of `<module>.deletion` (nothing imports it
today, so the hook would be dormant anyway). Deferred rather than block the
schema on a cross-team change. Tracked as a follow-up.

**Persist the assembled `IntelligenceSnapshot` in an `intel_snapshots` table.**
Rejected: the dashboard re-queries the component tables on each request;
`IntelligenceSnapshot` stays an event payload only. There is no seventh table.

## Consequences

- A team's weekly report survives the deletion of one of its source meetings
  until the retention window drops it. Accepted as a gap until the follow-up
  lands. `intel_reports` holds team-level aggregates and LLM prose generated
  from computed numbers — no per-person data — so the privacy exposure of the
  gap is small; the compliance rule in `../architecture/privacy.md` §4 is the
  reason it is a tracked follow-up rather than a "won't do".
- `depends_on` couples the `intelligence` branch to a specific `core` revision
  id. ADR and migration history are append-only, so the id is stable; if `core`
  ever renumbered it the pin would break.
- The registry defect this surfaced is not E's alone: any module that registers
  a deletion hook touching the database will break the same core test. Worth
  fixing in `packages/core` once, not per module.
