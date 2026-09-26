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
| `team_integrations` | One team's connection to Notion, Slack or Calendar |
| `meetings` | One analysis unit |
| `participants` | One voice at a meeting, identified or not — usually one person, not always; see below |
| `utterances` | One continuous stretch of speech, PII-masked |

### Write ownership

**Module A writes shared entities. B, C, D, E read them.**

B, C, D, E must not issue `INSERT`, `UPDATE`, or `DELETE` against `meetings`,
`participants`, or `utterances`. If you need to record something about an
utterance — a classification, a topic assignment, a score — put it in your own
prefixed table with a foreign key to `utterances.id`.

`users`, `teams`, and `team_members` are written by the auth layer in
`packages/core`, not by any module.

`team_integrations` is written by `packages/core` as well, from the settings
screen (S28). Modules read it and never write it:

```python
from autune_core import load_integration

config = load_integration(session, meeting.team_id, "notion")
if config is None:
    return  # this team has not connected Notion; skip the feature
client = NotionClient(config.require_secret())
```

Ask per call rather than caching the result — a team can disconnect a service
between two meetings. Credentials belong to the customer team, not to the
deployment, which is why they are not environment variables: one deployment
serves many teams, and each points Autune at their own workspace. `secret` is
Fernet ciphertext (`autune_core.crypto`), so a database dump is not a set of
working tokens.

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

### A participant row is a voice, not a person

Module A writes one `participants` row per speaker label diarization produces,
before anyone is identified. Splitting one voice into two clusters is
diarization's characteristic failure — it is what module A's DER measures — so
once identification (#6) fills `user_id`, **one person can own several
participant rows in the same meeting.** Before identification, two unidentified
rows may be two people or one, and nothing in the data says which.

Anything that counts people or says something per person has to account for
this. It has already broken two ways:

- **Counting people** (E, #128). Two rows for one person made a two-person
  meeting look like three, which passed the small-meeting gate on speaking
  ratio and let a co-attendee derive the other person's exact share as
  `1 - own`. E now counts only rows resolved to a `user_id` for the gate
  (`speaker_count_for_gate` in `autune_intelligence.speaking`): undercounting
  is the direction that cannot leak.
- **Per-person verdicts** (C, #164). A person split in two was reported as
  having spoken on a topic *and* been silent on it, and a gap raised on that
  silence is a false statement about somebody who spoke. C merges rows sharing
  a `user_id` when it builds the report, and having spoken as any of them
  counts as having spoken.

The rule for a consumer:

- **Group by `user_id` where it is set.** A row with no `user_id` stands for
  itself — and is not assumed to be a *different* person from anyone.
- **Where a count protects someone** — a gate, a ratio, a small-group
  statistic — count only identified people.
- **Store per row if the speaker track is your unit of analysis; merge where
  the result leaves your module.** A stored merge cannot be undone when an
  identification is corrected.
- **Do not let `user_id` stand for the group in anything you publish** unless
  the consumer needs identity across meetings. A participant id is scoped to one
  meeting, so a payload carrying one holds no cross-meeting identity on its own
  — but any module may read the shared `participants` table and resolve it, so
  accumulating one person's record across meetings is a `privacy.md` section 3
  violation on the consumer's side rather than something the id format prevents.
  C represents a merged person by the smallest of their participant ids
  (`contracts.md`, `GapReport`).

Identification can land after your task ran — a speaker confirms their label by
Slack DM later. A result computed per request (E's `/me`) corrects itself; one
computed once at pipeline time (C's report) reflects `user_id` as it was then.

## Module tables

Every table a module owns is named `<prefix>_<name>`.

| Module | Prefix | Examples |
| --- | --- | --- |
| A. audio | `aud_` | `aud_jobs`, `aud_speaker_embeddings`, `aud_masking_events`, `aud_consent_attestations` |
| B. extraction | `ext_` | `ext_classifications`, `ext_action_items`, `ext_external_refs` |
| C. gap | `gap_` | `gap_topics`, `gap_gaps`, `gap_participation` |
| D. context | `ctx_` | `ctx_materials`, `ctx_topic_links`, `ctx_decisions`, `ctx_decision_versions` |
| E. intelligence | `intel_` | `intel_scores`, `intel_predictions`, `intel_reports` |
| *(proposed #260)* agent layer | `agent_` | `agent_work_items`, `agent_runs` |

`agent_` is proposed in #260 and is the one prefix that does not belong to a
module. A prefix marks an owner; under that proposal an owner is a module *or*
the agent layer (ADR 0009). Everything else about the rule is the same — the
tables are owned by one party, nobody else writes them, and they need a
deletion path by `meeting_id` or `user_id` like any other derived table.

Both proposed tables need that path, and **`agent_runs` needs it as much as
`agent_work_items` does**: its `steps`, `decisions` and suspended `messages`
hold copies of what the modules' tools returned, so it carries `meeting_id`,
cascades from `meetings`, and is swept at retention expiry. A copy that outlives
what it copied is how a value one module blanked comes back alive somewhere
else. Raised on #261 by the owners of B and D.

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
  derived from them. **Under review — see ADR 0007**, which would make departure
  an access change that clears `participants.user_id` and keeps the meeting's
  record. Until that ADR is accepted or rejected, this bullet is what the code
  follows, and `privacy.md` section 4 says the same.
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
