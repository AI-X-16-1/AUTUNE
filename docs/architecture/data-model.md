# Data Model

## Datastores

| Store | Purpose | Who uses it |
| --- | --- | --- |
| **PostgreSQL** | Shared entities, all module tables, history, embeddings via pgvector, topic graphs and decision lineage as rows | Everyone |
| **Redis** | Celery broker and result backend, short-lived cache | Everyone, through `autune_core` |
| **Object storage / local temp** | Uploaded recording during processing only | A, transient only |

Three stores, and two of them are infrastructure. Never invent a fourth. If you
need one, that is an ADR.

Embeddings and graphs are PostgreSQL rows, not separate services. That is
deliberate: they cascade on meeting deletion like everything else, instead of
needing their own cleanup path. See
`../decisions/0004-pgvector-over-chroma.md` and
`../decisions/0005-no-graph-database.md`.

## Shared entities — `packages/core`

Defined once, in `packages/core/src/autune_core/entities.py`. No prefix — the
absence of a prefix is what marks a table as shared.

| Table | Meaning |
| --- | --- |
| `users` | A person with an account |
| `teams` | An organization or squad |
| `team_members` | User ↔ team membership and role |
| `meetings` | One analysis unit |
| `participants` | A person present at a meeting, identified or not |
| `utterances` | One continuous stretch of speech, PII-masked |

### Write ownership

**Module A writes shared entities. B, C, D, E read them.**

B, C, D, E must not issue `INSERT`, `UPDATE`, or `DELETE` against `meetings`,
`participants`, or `utterances`. If you need to record something about an
utterance — a classification, a topic assignment, a score — put it in your own
prefixed table with a foreign key to `utterances.id`.

`users`, `teams`, and `team_members` are written by the auth layer in
`packages/core`, not by any module.

This is not a style preference. Two modules writing the same row is the
failure mode this whole structure exists to prevent.

### Sketch

```
users ──< team_members >── teams
  │                          │
  │                          └──< meetings
  │                                  │
  └──< participants >────────────────┤
             │                       │
             └──< utterances >───────┘
```

`utterances` columns that downstream modules rely on: `id`, `meeting_id`,
`participant_id`, `speaker_label`, `start_sec`, `end_sec`, `text` (masked),
`confidence`. Adding a column here is a shared-entity change: announce it.

## Module tables

Every table a module owns is named `<prefix>_<name>`.

| Module | Prefix | Examples |
| --- | --- | --- |
| A. audio | `aud_` | `aud_jobs`, `aud_speaker_embeddings`, `aud_masking_events` |
| B. extraction | `ext_` | `ext_classifications`, `ext_action_items`, `ext_external_refs` |
| C. gap | `gap_` | `gap_topics`, `gap_gaps`, `gap_participation` |
| D. context | `ctx_` | `ctx_materials`, `ctx_topic_links`, `ctx_decisions`, `ctx_decision_versions` |
| E. intelligence | `intel_` | `intel_scores`, `intel_predictions`, `intel_reports` |

A table without a prefix is a shared entity. If you are creating one, you are
either mistaken or you need team approval.

Graphs and embeddings follow the same rule: they are ordinary prefixed tables.
A topic graph is `gap_topics` plus `gap_topic_edges`; embeddings are
`ctx_embeddings` with a `vector` column.

The `vector` extension is enabled by a `packages/core` migration, because
`CREATE EXTENSION` is database-level. A module's embedding table chains onto
that; do not enable the extension from a module migration.

## Foreign keys across module boundaries

Referencing a **shared entity** is expected:

```python
meeting_id = Column(String, ForeignKey("meetings.id", ondelete="CASCADE"))
utterance_id = Column(String, ForeignKey("utterances.id", ondelete="CASCADE"))
```

Referencing **another module's table** is forbidden. It creates a migration
ordering dependency between two independent Alembic branches and couples two
owners' schemas. If E needs to point at one of B's action items, store the ID as
a plain string column with no foreign-key constraint, populated from the
`ExtractionResult` contract.

```python
# in intel_scores — correct
source_action_item_id = Column(String, nullable=True)  # no ForeignKey

# forbidden
source_action_item_id = Column(String, ForeignKey("ext_action_items.id"))
```

## Conventions

- Primary keys: prefixed strings (`mtg_…`, `utt_…`, `act_…`). Generated in
  `autune_core`, not per module.
- Timestamps: `created_at`, `updated_at`, both `TIMESTAMP WITH TIME ZONE`, UTC.
- Soft deletes: do not use them. Privacy requires real deletion — see
  `privacy.md`.
- Enums: store as strings with a check constraint, not as PostgreSQL enum types.
  Adding a value to a PG enum requires a migration lock; strings do not.
- JSON columns: `JSONB`. Acceptable for model outputs and raw scores. Not
  acceptable for anything you will filter or join on.
- Indexes: every `meeting_id` column gets an index. You will query by meeting.

## Retention and deletion

Every module-owned table must be reachable from a `meeting_id` or a `user_id`,
because both must be deletable on request:

- **Meeting deletion** cascades from `meetings` through shared entities. Module
  tables that reference `meetings.id` with `ON DELETE CASCADE` are handled
  automatically — embeddings included, since they are rows. Tables that store a
  `meeting_id` without a constraint must be cleaned up by the module's own
  deletion hook.
- **User deletion / team departure** removes that user's utterances and anything
  derived from them.
- **Retention sweep** deletes analysis results past the retention window (90
  days by default).

Everything a module owns is a PostgreSQL row, so meeting deletion cascades
reach all of it. Register a hook in `autune_core`'s deletion registry only for
something kept outside the database — a cached artifact, a file on disk. A table
that cannot be cleaned up is a compliance defect. See `privacy.md`.

## Migrations

Each module owns an independent Alembic branch. Never write a migration that
touches a table you do not own — including shared entities, which are migrated
from `packages/core`. Runbook: `../engineering/migrations.md`.
