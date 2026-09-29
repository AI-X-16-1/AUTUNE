# Module D. Meeting Context Engine

| | |
| --- | --- |
| **Package** | `autune_context` |
| **Owner** | 문민재 |
| **Backend** | `modules/context/` |
| **Frontend** | `apps/web/src/features/context/` |
| **Table prefix** | `ctx_` |
| **API prefix** | `/api/context` |
| **Alembic branch** | `context` |

## Responsibility

Keep context alive across meetings. Link the current meeting's topics to past
meetings, and track how a decision changed over time. Shortly before a
scheduled meeting, send a brief that recaps the meeting it follows. In Phase 2,
analyze uploaded material into an agenda.

D is what makes Autune more than a transcription tool. Everything else processes
"this meeting"; D connects meetings to one another.

## Non-goals

- Extracting decisions from a single meeting — B classifies them; D only tracks
  how they change. See "The B → D boundary" in `../architecture/contracts.md`.
- Detecting what was missing within one meeting — that is C.
- Team-level analytics — that is E.
- Building any Phase 2 feature during the six weeks (material analysis, agenda
  generation, the S22 relationship graph). The pre-meeting brief moved into the
  build on 2026-09-29 — see "Pre-meeting brief".
- Calling Jira. B owns the Jira integration; the brief's agenda comes from B
  through a contract, not from D calling Jira itself.

## Inputs

| Source | Contract or table | Notes |
| --- | --- | --- |
| A | `TranscriptReady` via `autune.transcript.ready` | Topic linking. Needs only the transcript, so it runs in parallel with B and C. |
| B | `ExtractionResult` via `autune.extraction.completed` | Decision lineage. Needs `result.decisions`, so it runs after B. |
| `packages/core` | `meetings`, `participants`, `utterances` | Read-only. Never `INSERT`/`UPDATE`/`DELETE`. |
| Own history | `ctx_decisions`, `ctx_decision_versions`, `ctx_embeddings` | Past meetings' topic embeddings and decision threads. |
| Web upload | Material documents (PDF, docx, markdown) | Phase 2 only. |

## Outputs

| Destination | Contract | Event |
| --- | --- | --- |
| E | `ContextLinks` | `autune.context.completed` |
| Slack | Topic-link notice, decision-drift warning | — |
| Slack | Pre-meeting brief | — (on a clock: `autune.context.periodic.send_due_briefs`) |

`ContextLinks` carries only `asserted` and user-`confirmed` links. `pending`
links (below the confidence threshold, awaiting user confirmation) live in
`ctx_topic_links` and are served through the API, not published to E. No
contract change is needed for the confirmation flow.

## AI stack

The three trained models sit behind interfaces (see "Model abstraction layer")
and are self-hosted. The LLM is Phase 2 and is declared as an interface only.

| Component | Model or algorithm | Hosting | Version pin |
| --- | --- | --- | --- |
| Dense retrieval | KURE-v1 (inherits bge-m3's dimension) | Self-hosted HTTP | HF revision, recorded per row |
| Lexical retrieval | BM25 over a kiwipiepy tokenization | In application code | `rank-bm25`, `kiwipiepy` pinned in the manifest |
| Re-ranking | `dragonkue/bge-reranker-v2-m3-ko` | Self-hosted HTTP | HF revision, recorded per row |
| Decision-change detection | `klue/roberta` fine-tuned on KorNLI (in-house) | Self-hosted HTTP | Training job in `modules/context/scripts/`; checkpoint id recorded per row |
| Agenda generation | LLM | Phase 2, through `autune_integrations` | — |
| Pre-meeting brief | Template over D's own rows; the previous-meeting choice reuses retrieval and re-ranking | — | — |

PostgreSQL full-text search has no Korean analyzer without a further extension,
so hybrid retrieval is **not** a single query: the vector similarity plus its
metadata filter (`team_id`, retention window) run in PostgreSQL through pgvector,
and BM25 runs in application code. The two rankings are fused with reciprocal
rank fusion (RRF).

**Retrieve broad, re-rank narrow.** Top 50 from hybrid retrieval, top 10 after
re-ranking. Mis-linking is this module's main risk. The evaluation set showed
the cross-encoder is the wrong judge of *same topic*: it answers "does this
passage answer this query", and scores a past meeting on the same subject but
with different content — the plan vs. its status update, the same issue in
other words — near zero. So a link is asserted on dense segment similarity
(`link_similarity_threshold`) *or* a confident re-ranker score
(`link_confidence_threshold`); below both, offer it for user confirmation
rather than asserting it.

### Topic-statement extraction is not an LLM task (MVP)

The linking pipeline is `topic → embed → hybrid retrieve → re-rank → link`.
Accuracy is bought by retrieval and re-ranking, not by the polish of a topic
label, and what is matched across meetings is the topic **embedding** — as long
as both sides use the same extraction method, they stay consistent.

For the MVP, topics are extracted without an LLM:

1. **Segment** the transcript with an embedding-based TextTiling: a sliding
   window over consecutive utterance embeddings, cut at local similarity minima,
   with sub-minimum-length segments merged.
2. **Label** each segment with its most repeated noun phrase. Phrases are
   runs of whitespace-separated words that kiwipiepy reads as starting with a
   content noun — word-level, so a word missing from its dictionary (온보딩)
   survives whole. Numbers with counters (5장), one-letter nouns, names with an
   honorific (민재님) and nouns every meeting shares (오늘, 회의, 확인) break a
   phrase. The score is words × occurrences among the repeated phrases, so a
   phrase said twice outranks one of its words said three times (#352: one-word
   labels such as `장` or `10` gave the re-ranker nothing to score). A segment
   with no noun phrase at all is small talk and is dropped, not labelled with a
   snippet of itself. There is no background-corpus weighting.
3. **Represent** each topic for matching as the segment's mean-pooled embedding,
   plus the ids of the utterances it was cut from. The re-ranker reads those
   utterances' text back from `utterances`; BM25 still matches on labels.

This keeps the core path fully on self-hosted infrastructure, keeps the
evaluation deterministic, and avoids sending a full transcript to an external
API. An optional Phase 2 step may use the LLM to rewrite the **display** label
only — never the matching representation.

## Model abstraction layer

Every AI model this module uses sits behind a `typing.Protocol` in
`autune_context/pipeline/`. The concrete implementation — self-hosted HTTP or
in-process weights — is selected by a config string and is never referenced
directly outside that package.

```
autune_context/pipeline/
├── __init__.py     # public surface: get_embedder / get_reranker / get_nli,
│                   #   plus an opt-in worker_process_init hook that warms and logs each model
├── base.py         # Protocols: Embedder, Reranker, NliModel, LlmClient (+ result dataclasses)
├── registry.py     # config string → implementation, lru_cache, startup dimension guard
├── _serving.py     # shared /health + /info probe for the self-hosted HTTP clients
├── embedding.py    # KureHttpEmbedder / KureLocalEmbedder / FakeEmbedder
├── reranking.py    # BgeRerankerKoHttp / ...Local / Fake
├── nli.py          # KlueKorNliHttp / ...Local / Fake
├── retrieval.py    # HybridRetriever: KURE dense + BM25, RRF fusion
└── topics.py       # TextTiling segmentation + keyphrase labelling
```

Rules:

- **Selection is config.** `AUTUNE_CONTEXT_EMBEDDER_IMPL`, `_RERANKER_IMPL`,
  `_NLI_IMPL`. Swapping an implementation changes no code outside `pipeline/`.
- **Load once at worker startup**, not per task — `registry.get_*()` is
  `lru_cache`d and warmed from `worker_process_init` when
  `AUTUNE_CONTEXT_WARM_MODELS_ON_WORKER_INIT=true`. Opt-in, not automatic:
  `apps/worker` imports every module's `tasks.py` into one Celery app, so an
  unconditional hook would run in every worker process regardless of `-Q` —
  including `gpu`/`default` workers that never see a context task and cannot
  reach the context model endpoints. Set it only on workers that consume
  `cpu_heavy`.
- **`model_version` is read from the serving endpoint** (`/info`) at warm-up and
  written onto every output row, so results stay traceable across redeploys. The
  `*Http` client also probes `/health` in its constructor, so an unreachable
  endpoint fails the warm-up rather than the first task.
- **The embedding dimension is a compile-time fact.** `ctx_embeddings.embedding`
  is `vector(N)` where `N = autune_context.constants.EMBEDDING_DIM`, fixed at
  migration time. `get_embedder()` raises at startup unless the chosen model's
  `dim` equals it. The `*Http` embedder reads its dimension from `/info`, so the
  guard covers the self-hosted impl too, not only `*_local`.
  `AUTUNE_CONTEXT_EMBEDDING_DIM` is an operator-visible mirror of the same value;
  a unit test fails if the two drift. A re-dimensioned model is a new migration,
  not a config change.
- **`*_local` implementations are optional.** They pull `sentence-transformers`,
  `torch` and `transformers`, which live in the `local-models` optional
  dependency group and are used only for local development, CI-free runs, and
  evaluation. The worker image runs the `*_http` implementations.
  `scripts/up.sh` runs the embedder and re-ranker this way, and the NLI step
  too under `--real-models`; the fakes are not a lighter demo of this module —
  the fake embedder is a hash of the text, so it links next to nothing.
- **`Fake*` implementations** back unit tests; integration and pipeline tests
  select them with `AUTUNE_CONTEXT_*_IMPL=fake`.

### The LLM client is declared, not implemented (PR #90 review)

`LlmClient` is a Protocol only. It has no Phase 1 implementation and no config
knob. It is the one path that would leave our infrastructure, so it must be
built on top of `autune_integrations` (or a shared LLM client added there) —
`check_outbound` has to run on every call. That is invariant 11: a docstring
saying "masked text only" is not the guard. A module-local `httpx` client
(`ExternalLlm`, defaulting to OpenAI) was written for the scaffold and removed
in review — an unused external client with a third-party default is the worst
state. It comes back in Phase 2 with the agenda/brief work, done through the
integration boundary.

### Self-hosted serving contract

The three self-hosted models are reached over HTTP. Each endpoint exposes
`/health`, `/info` (returns the model id and revision), and its inference route
(`/embed`, `/rerank`, `/nli`). Deployment of these services is owned by `infra/`
and is settled in Phase 0.

## Pipeline

### Topic linking — from `autune.transcript.ready`

1. `require_privacy_guarantees()` on the payload; refuse an undeleted-audio or
   unmasked transcript.
2. Keep only a consenting speaker's utterances (`privacy.md` section 5): those
   whose `participants` row has `consented = true`. An utterance with no
   participant is out — unknown is not yes. This is the line B and C already
   draw (#163); `TranscriptReady` carries every utterance and each consumer
   filters. Nothing below sees the rest: not the embedder, a label, BM25, the
   re-ranker, or `utterance_ids`. A meeting nobody consented to gets no topics
   and no links, and still marks topic linking done.
3. Extract topics (`pipeline/topics.py`), embed them with KURE-v1, persist to
   `ctx_embeddings` (`kind = "topic"`).
4. Hybrid retrieval over past meetings of the same team, within the retention
   window: pgvector cosine + in-process BM25, fused with RRF, top 50.
5. Re-rank those 50 with the cross-encoder, keep the top 10. Each candidate
   meeting is scored on the text of its topic segment closest to this topic
   (`ctx_embeddings.utterance_ids`, read from `utterances`), or on that
   segment's label for a row that predates the column. The text is read only
   where its speaker still consents — checked again at read time, so a
   withdrawal takes effect without re-running the past meeting (the label is
   the fallback, as for deleted utterances).
6. At or above `link_similarity_threshold` (dense cosine between the two
   segments) or `link_confidence_threshold` (re-ranker), write an `asserted`
   link; below both, write a `pending` link for the user to confirm. The top 10
   kept are the 10 with the highest of the two scores, stored as `confidence`.
7. Mark `ctx_meeting_status.topic_linking_done`, then schedule
   `autune.context.publish_if_ready` with a countdown of
   `publish_timeout_s`. If the meeting had already published (module A
   reprocessed the recording), schedule `autune.context.republish` instead —
   see "Republishing after a rerun".

### Decision lineage — from `autune.extraction.completed`

Input is `ExtractionResult.decisions`. B owns *what counts as a decision in this
meeting*; D owns *whether it is the same decision as one from before*. **D does
not run a decision classifier.** Duplicating B's would make the two disagree,
and a decision would then show in the summary tab (S15) while missing from the
lineage view (S22), which reads to a user as a bug.

1. Each of B's decisions (`dec_` id) is embedded and matched to the most
   similar existing thread's *chronologically latest* statement — by the
   matched meeting's `started_at`, not by which version was inserted last —
   cosine ≥ `lineage_match_threshold` (`AUTUNE_CONTEXT_LINEAGE_MATCH_THRESHOLD`,
   default `0.65`, tuned in eval). Every (decision, thread) pairing in the
   meeting is scored up front and assigned strongest-first, so a weak match
   earlier in `result.decisions` can't grab a thread out from under a much
   stronger match later in the list. No thread above the threshold opens a new
   one, anchored on the meeting's team. One thread takes at most one of this
   meeting's decisions. A meeting past its retention window is excluded from
   matching — see "Deletion".

   **Matching happens before this meeting's own previous versions are deleted.**
   B's `dec_` id is stable across a rebuild whose sources did not change and
   fresh only when they did (`autune_extraction.decisions.decision_id`, #171) —
   which includes every reprocess in module A, since that mints new `utt_` ids
   (#194) — but D never matched on that id in the first place: whether a decision
   is the same one as before is D's question, not B's (#25), so matching runs by
   wording regardless of which way B's id moved. A *solo* thread (no other
   meeting's version to rediscover it by similarity) has nothing but that wording
   to compare against. Deleting the meeting's old versions first would erase the
   one piece of evidence — the meeting's own about-to-be-replaced statement —
   that lets a rebuild with materially unchanged wording land back on the same
   thread instead of forking a new one on every reprocess. This meeting's own
   pre-delete versions are *added* to the
   matching candidates, not substituted for the thread's team-wide head: a
   thread's head is always its single chronologically-latest version, so a
   meeting sitting in the *middle* of a thread compares against a later
   meeting's (possibly quite different) wording unless its own version is
   offered as a candidate too. A thread can therefore appear twice among the
   candidates for one reprocessed meeting — once as the team-wide head, once
   as the meeting's own version — and still take at most one of this
   meeting's decisions; the two entries are for the same slot.
2. Every thread this meeting's decisions touched is then **re-chained end to
   end**, not just appended to: order its versions by meeting time and run NLI
   on each adjacent pair in both directions (`pipeline/change.py`):
   - Entailment either way → `unchanged` — unless only the later statement
     entails the earlier one *and* it narrows, adds or extends something new
     (만/도/까지 on a noun the earlier one never mentioned) → `modified`.
   - A later statement that keeps something (계속/그대로/유지) is re-checked
     with those words stripped: "B사를 계속 쓴다" after "A사 대신 B사로 바꾼다"
     contradicts the act of switching, but "B사를 쓴다" is entailed by it →
     `unchanged`. A real revert ("A사를 계속") still contradicts.
   - Otherwise a contradiction either way → `reversed` if the later statement
     negates, stops or cancels/replaces (않/말/안/못/없이/아니, 그만두다·접다,
     취소·폐지·보류·백지화·대신…), else `modified` — a moved date or amount
     contradicts the old one exactly as hard as a cancellation, so NLI alone
     cannot tell them apart.
   - Anything else → `modified`.

   `nli_label` keeps the model's own forward verdict. The chronologically-first
   version is `new`. Re-chaining (rather
   than only linking the new version onto whatever was previously "latest") is
   what keeps the lineage correct when B reports meetings out of order — a
   longer meeting finishing after a shorter later one, a backfill — and what
   repairs a later version's chain when an earlier meeting is re-processed.
3. Each `ctx_decision_versions` row records what changed, in which meeting,
   chained onto its chronological predecessor via `previous_version_id`.
   `confidence` is the NLI score that decided the change type for a non-first version,
   and B's own decision confidence for the chronologically-first one; `nli_label`
   is null for that first version. `confidence` is not recomputed back to B's
   number if a version later becomes its thread's first version again (e.g. an
   earlier meeting is deleted) — same stance as `previous_statement` below:
   what changed survives, only wording tied to a specific meeting is corrected.
4. Compute `key_stakeholders_absent` from the shared `participants` of each
   version's meeting against the thread's known stakeholders (users across
   every earlier version's meeting). A non-empty list drives the drift warning.
   **Only once every speaker is named.** If the version's meeting has no
   participant rows, or any participant whose `user_id` is still NULL, the list
   is empty: that voice could be any of the known stakeholders. Module A names a
   speaker only when a person confirms it in the app (#370), after this step has
   run, so a meeting is usually unconfirmed here. Recording the resolved ids
   alone would mark people absent from a meeting they spoke in and send each of
   them a drift DM saying so. The accepted cost is a missed warning: a meeting
   with a guest who never resolves to an account never reports an absence, and
   a confirmation made later is not picked up until the thread is next
   re-chained (the re-trigger is #360's question). "Every speaker named" is not
   a roll call either: module A writes a participant row only for a speaker
   label that spoke, so a stakeholder who attended without speaking has no row
   and is still counted absent — a false DM this rule narrows but does not
   remove.
5. Mark `ctx_meeting_status.lineage_done` (and `extraction_seen`), then call
   `autune.context.publish_if_ready` — or `autune.context.republish` when
   the meeting had already published with its lineage (a rerun of B; see
   "Republishing after a rerun").

Idempotent: a re-run replaces the meeting's `ctx_decision_versions` row(s) and
re-chains every thread that touches, then runs all three deletion sweeps (see
"Deletion") — global and idempotent, so running them on every call closes real
gaps ahead of #87 rather than leaving them for tests to be the only caller.
The rerun meeting's own `ContextLinks` is republished (see "Republishing after
a rerun"). Re-chaining a thread updates other meetings' versions too (an
earlier meeting arriving late shifts what a later one's `previous_*` point
to); *their* already-published `ContextLinks` are not re-emitted — E ends up
with a stale `decision_lineage` for those meetings until something republishes
them. Not done yet; tracked for a later phase.

**Concurrency.** `cpu_heavy` is a concurrent queue (docs/architecture/async-
pipeline.md): two meetings for the same team can call `build_decision_lineage`
at once. Under READ COMMITTED, matching against the same thread head without
coordination lets both meetings chain onto whatever was "latest" before either
committed, so one meeting's version silently drops out of the chain. D holds a
`pg_advisory_xact_lock` keyed on the team for the duration of the transaction —
scoped to one team, so two different teams' meetings still process fully in
parallel.

### Publishing — `autune.context.publish_if_ready`

Publish `ContextLinks` when `topic_linking_done` is set **and** either
`lineage_done` is set or the timeout has elapsed. If B has not reported by the
timeout, publish with an empty `decision_lineage` and `"extraction"` in
`missing_sources`. **A failure in B must never cost the user their topic links.**
`published_at` guards against a double publish; the task is safe to run twice.

### Republishing after a rerun — `autune.context.republish`

The `published_at` guard also refuses a meeting whose content changed after it
published. Module A reprocesses a recording by emitting
`autune.transcript.ready` again with new `utt_` ids, so both halves rerun: topic
linking rebuilds the links, and B re-emits `autune.extraction.completed` with
`dec_` ids that moved with their sources. Without a republish E keeps the old
payload, including `dec_` ids B no longer has.

Each half reads, under the `ctx_meeting_status` row lock, whether the meeting
had already published before its own write. If it had, it enqueues
`autune.context.republish`, which re-sends `ContextLinks` built from the rows
as they are now and **sends no Slack notice** — the notices belong to the first
publish, and a reprocess is not news to the team. `build_decision_lineage`
reports this as `LineageOutcome.REBUILT`, distinct from `LATE` (the B-timeout
fallback published *without* lineage, so the drift warning is still owed and
goes out through `notify_late_drift`) and `FIRST` (nothing published yet).
`republish` never performs a first publish: for a meeting with no
`published_at` it does nothing, so the first `ContextLinks` always goes through
the gate above with its notices.

An A reprocess therefore republishes twice, once per half, in whichever order
they finish. Each is built from committed rows under the row lock, so the
later send is the fresher one and carries both reruns once both have
committed. E stores the last `ContextLinks` it processes; two workers taking
the pair out of order would leave the older one — the same exposure the
late-lineage republish already has, and not closed here. Closing it needs E to
tell an older payload from a newer one, and `ContextLinks` carries no version or
timestamp to do that with — a contract change (invariant 5), not a D-side fix.

### Pre-meeting brief — `autune.context.periodic.send_due_briefs`

A clock, not an event: every minute, `send_due_briefs` finds meetings with
`status = 'scheduled'` whose `started_at` falls within the next
`brief_lead_minutes` (default 10) and that have no `ctx_briefs` row, and
enqueues `send_brief` for each. A scheduled meeting is one module A created
ahead of time (`POST` with a future `started_at`); D reads it and writes
nothing to it. A meeting already started — its start has passed, or A moved
its status on because a recording began — gets no brief, so a brief missed
while the worker was down stays missed rather than arriving mid-meeting.

**Which past meeting it recaps**, first match wins:

1. `series` — the team's most recent analyzed meeting with the same title
   (whitespace and case normalized). A recurring meeting is the common case,
   and its title names the series rather than a topic, so retrieval alone
   would miss it.
2. `topic` — the title and agenda run through the same hybrid retrieval and
   re-ranking as topic linking, and a candidate is asserted on the same rule a
   topic link is: dense similarity ≥ `link_similarity_threshold` or re-rank
   score ≥ `link_confidence_threshold`. The stronger of the two picks among
   the confident candidates.
3. `latest` — the team's most recent analyzed meeting. Also the fallback when
   a model call in step 2 fails: a brief retried until the meeting starts is
   one that never arrives.

"Analyzed" means `ctx_meeting_status.topic_linking_done`, and every candidate
passes `visible_meeting_clauses` — a meeting past its retention window is never
recapped.

**What it says.** The past meeting's topic labels (`ctx_embeddings`) and
decision statements with their change type (`ctx_decision_versions` —
"(번복)", "(변경)"), then the issues this meeting is expected to take up.
Nothing is generated. Every list is capped and every item clipped
(`notify.MAX_BRIEF_*`, `BRIEF_*_CHARS`) so the largest possible brief stays
under `autune_integrations`' 4,000-character outbound limit, which refuses a
post outright rather than trimming it.

**The agenda comes from Jira, through B.** Jira is module B's integration, and
D neither calls Jira nor reads B's tables. The interface is not agreed yet, so
`briefs.agenda_for` returns nothing and the brief says "이번 회의에 연결된 안건이
없습니다". What D needs, per scheduled meeting or per team: an issue's title,
key, status and URL. That is a contract addition in `packages/contracts`
agreed with B's owner — see "Open questions".

**Stores the choice, not the recap.** `ctx_briefs` keeps `previous_meeting_id`
and `match_reason`; the recap is rendered from the past meeting's rows every
time it is read (the Slack post, `GET /briefs/{meeting_id}`). A copy would
outlive the meeting it quotes — the problem `previous_statement` already needs
a sweep for. When the past meeting is deleted (`SET NULL`) or expires, the
brief reads "지난 회의는 보존 기간이 지나 삭제되었습니다" instead;
`match_reason` set with no readable recap is how that case is told apart from
"there was no past meeting".

**Claim, then send.** `compose_due_brief` inserts the `ctx_briefs` row with
`ON CONFLICT DO NOTHING` — the insert is the claim, so overlapping periodic
runs and a meeting enqueued on several ticks send once — and stamps `sent_at`
in the same commit, before the Slack post. A failure before that commit rolls
the claim back and the next tick retries; a worker lost after it loses the
brief rather than sending it twice, the trade the other notices make. A team
with no Slack channel still gets the row (readable in the app) with `sent_at`
left `NULL`.

**In the app**, the same brief sits at the top of S15's context tab
(`features/context` `BriefPanel`), rendered from `GET /briefs/{meeting_id}`. A
meeting with no brief — every finished meeting, and a scheduled one until its
brief is composed — draws nothing there, and the tab reads as it did before.

## Storage

PostgreSQL only. Every row is reachable from a `meeting_id`, a `team_id`, or is
cleaned up by a deletion hook (see "Deletion").

| Table | Purpose | Key columns | Anchor / deletion |
| --- | --- | --- | --- |
| `ctx_embeddings` | Topic (and, Phase 2, material) embeddings | `kind`, `ref_label`, `utterance_ids` (JSONB, nullable), `embedding vector(N)`, `model_version` | `meeting_id` FK `ON DELETE CASCADE` |
| `ctx_topic_links` | Meeting-to-meeting topic links with scores | `topic_label`, `linked_meeting_date`, `similarity`, `rerank_score`, `confidence`, `status` (`asserted`/`pending`/`confirmed`/`rejected`), `retriever_version`, `reranker_version` | `meeting_id` FK `CASCADE`; `linked_meeting_id` FK `ON DELETE SET NULL` |
| `ctx_decisions` | Decision threads (lineage identity, spans meetings) | `id` (`thr_`), `topic_label` | `team_id` FK `CASCADE`; orphan sweep deferred (#87) |
| `ctx_decision_versions` | Each version of a decision | `source_decision_id` (`dec_`, no FK), `previous_version_id` (self-FK), `current_statement`, `previous_statement`, `previous_meeting_id` (no FK), `change_type`, `nli_label`, `confidence`, `key_stakeholders_absent` (JSONB), `nli_version` | `thread_id` FK `CASCADE`, `meeting_id` FK `CASCADE` |
| `ctx_meeting_status` | Completion tracking for the two halves | `topic_linking_done`, `lineage_done`, `extraction_seen`, `deadline_at`, `published_at`, `notified_at` | `meeting_id` FK `CASCADE` |
| `ctx_briefs` | One pre-meeting brief per scheduled meeting: which past meeting it recaps, and whether it went out. The choice, never the recap | `previous_meeting_id`, `match_reason` (`series`/`topic`/`latest`), `sent_at` | `meeting_id` FK `CASCADE`; `previous_meeting_id` FK `ON DELETE SET NULL` |
| `ctx_materials` | Uploaded documents and chunk metadata | — | Phase 2 — not created in the MVP |

Notes:

- The `vector` dimension is fixed at migration time, so the embedding model is
  chosen before the table is created (Phase 0). The `vector` extension is
  already enabled by a `packages/core` migration; the embedding table chains
  onto that. Index the column with HNSW. Index every `meeting_id` column.
- Enums are stored as strings with a check constraint, not PostgreSQL enum
  types.
- **No cross-module foreign keys.** `source_decision_id` is B's, stored as a
  plain string. `previous_meeting_id` references a meeting but is left
  unconstrained so a retention sweep on that meeting does not cascade into an
  unrelated thread's lineage; the reader treats a missing meeting as "gone".
- A lineage is a chain, not a graph: `ctx_decision_versions.previous_version_id`
  links each version to its predecessor. The read side orders by meeting time
  rather than walking that chain — see "API" below for why. The S22 graph
  visualisation is Phase 2 and is a frontend rendering concern.

### Why `ctx_decisions` is anchored on `team_id`

A decision thread's value is continuity across meetings. If it were anchored on
its origin meeting, the whole lineage would collapse the moment that meeting
hit the 90-day retention window — visible immediately in a demo. So the thread
is anchored on `team_id` (which still cascades on team deletion), each version
is anchored on its own meeting, and a thread whose last version has been deleted
is swept by `service.sweep_orphan_decision_threads`.

## Deletion

A meeting past its `expires_at` is treated as gone for lineage purposes
*before* it is actually deleted: `_thread_heads` and `_rethread` both filter on
`visible_meeting_clauses` (team + not expired, shared with `HybridRetriever`'s
own filter — see "AI stack"), so an expired-but-not-yet-deleted meeting is
excluded from decision matching and drops out of the chain, the same way it
already drops out of topic retrieval. Its content stops being copied into a
later version's `previous_statement` or a thread's `topic_label` the moment it
expires, not only once the retention sweep gets around to deleting the row.

Meeting deletion itself cascades through `meeting_id` foreign keys and reaches
`ctx_embeddings`, `ctx_topic_links`, `ctx_decision_versions` and
`ctx_meeting_status`. Three things are **not** covered by cascade:

- **Dangling topic links.** `linked_meeting_id` is `ON DELETE SET NULL`, so the
  link row survives with its label and date; the API and UI show "the linked
  meeting is gone" and never reconstruct its content from an embedding.
- **Orphaned decision threads.** Once a `ctx_decisions` row has zero remaining
  versions it is dead weight. `service.sweep_orphan_decision_threads(session)`
  removes every such row — a global, idempotent sweep.
- **Dangling `previous_statement`.** `previous_meeting_id` is deliberately
  unconstrained (see `CtxDecisionVersion`'s docstring) so a retention sweep on
  that meeting does not cascade into an unrelated thread's lineage — but
  `previous_statement` is a verbatim copy of that meeting's decision text, and
  it would otherwise outlive the meeting it came from. That is the same
  violation `ctx_embeddings` and the topic-link rule above both refuse.
  `service.sweep_dangling_previous_statements(session)` nulls `previous_statement`
  on any version whose `previous_meeting_id` no longer exists — `change_type`,
  `nli_label` and `confidence` are untouched, so "what changed" survives and
  only the deleted meeting's wording goes.
- **Stale `topic_label`.** `ctx_decisions.topic_label` is set from whichever
  decision opened the thread and is refreshed to the thread's current head
  every time `_rethread` touches it — but a thread nobody touches again after
  that meeting is deleted keeps quoting it forever otherwise. That is the same
  violation as `previous_statement`, on the one field `_rethread` cannot reach
  on its own. `service.sweep_stale_topic_labels(session)` refreshes every
  thread's `topic_label` to its current visible head, blanking it (`""`) for a
  thread every one of whose versions has expired.

  None of the three sweeps above is yet registered as an `autune_core.deletion`
  meeting hook. ADR 0008 found that a hook issuing a real `DELETE` breaks
  `packages/core`'s own unit tests, which run before migrations on a clean CI
  database and iterate every registered hook; the fix needs shared-owner
  changes tracked in #87. Module E hit the same wall with `intel_reports` and
  deferred the same way. Until #87 lands, `service.build_decision_lineage`
  calls all three itself at the end of every run (in addition to the
  integration tests calling them directly) — real cleanup on every meeting
  processed, not only when a test happens to exercise it. A thread whose
  labelling meeting was deleted since the last time *any* meeting for its team
  triggered a lineage build still holds that meeting's `topic_label` in the
  interim; every other exposure above is likewise bounded to "until the next
  sweep run," not indefinite.

A test that deletes a meeting and asserts every `ctx_*` row for it is gone —
threads included, after the sweep — is part of shipping the schema, not an extra.

> `modules/context/CLAUDE.md` originally said everything cascades and no deletion
> hook is needed. That holds for four of the five tables; `ctx_decisions` is the
> exception, and its sweep is deferred rather than wired (see above).

## API

All paths are relative to `/api/context`. Long work is already done by the time
these are called (the pipeline runs off Celery events), so these are reads plus
one mutation.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/links/{meeting_id}` | Topic links for a meeting, `asserted` and `pending` separated |
| POST | `/links/{link_id}/confirm` | User confirms or rejects a `pending` link (`status` → `confirmed`/`rejected`) |
| GET | `/decisions/{thread_id}` | Full lineage timeline, oldest version first |
| GET | `/decisions` | Filter by team, topic, change type |
| POST | `/materials` | Upload material — Phase 2 |
| GET | `/briefs/{meeting_id}` | A scheduled meeting's pre-meeting brief. 404 until it is composed (`brief_lead_minutes` before the start) |

`GET /decisions/{thread_id}` orders a thread's versions by meeting time
(`service._meeting_time`), the same key `_rethread` chains by — not by walking
`previous_version_id` from the chronologically-first version. A walk from the
root breaks the moment that version ages past the retention window without a
later meeting having touched the thread since: nothing re-chains it on a mere
expiry (see "Deletion" below), so the surviving versions' `previous_version_id`
still points at a now-invisible row, and a walk requiring a visible root would
find none and lose the rest of the thread with it. Ordering by meeting time
only ever drops the row that actually expired.

`GET /decisions` lists each thread by its current head only (the same
definition `_thread_heads` matches new decisions against) — `change_type`
filters on the head's own value, not any version in the thread's history; a
caller after the full drift record opens the thread with the route above.

Both routes' `topic_label` is derived from the head version's own
`current_statement`, not read off `ctx_decisions.topic_label`: that column is
a cache `_rethread` sets at write time and `sweep_stale_topic_labels` only
refreshes when the next lineage build touches the thread — neither runs on a
mere expiry, so it can still quote a version that just aged out of visibility
while an earlier, still-visible version is the true current head. `GET
/decisions`'s `topic` filter matches against that same live value, in Python
after head selection, for the same reason. `GET /links/{meeting_id}` and
`POST /links/{link_id}/confirm` apply the same expiry filter to the queried
meeting itself that the two decision routes already applied.

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |
| `autune.context.on_transcript_ready` | `autune.transcript.ready` | `cpu_heavy` |
| `autune.context.on_extraction_completed` | `autune.extraction.completed` | `cpu_heavy` |
| `autune.context.publish_if_ready` | after either half finishes, or on timeout | `cpu_heavy` |
| `autune.context.republish` | either half reran for a meeting that already published | `cpu_heavy` |
| `autune.context.notify_context_events` | after the first publish (`FIRST`, or the B-timeout fallback) | `default` |
| `autune.context.notify_late_drift` | after a `LATE` republish; never after `republish` | `default` |
| `autune.context.index_material` | material upload | `cpu_heavy` — Phase 2 |
| `autune.context.periodic.send_due_briefs` | every minute (`@periodic`) | `cpu_heavy` |
| `autune.context.send_brief` | enqueued by `send_due_briefs`, once per due meeting | `cpu_heavy` — the previous-meeting choice embeds and re-ranks |

Every task is idempotent: writes are keyed by `meeting_id` and applied as
delete-then-insert within one transaction, and `publish_if_ready` checks
`published_at`.

### Event publishing

D is the repository's first module to publish. A module may not import
`apps/worker`, and the async pipeline has no broker abstraction — a producer
calls its consumer's task by name (`async-pipeline.md`, "Payloads"). So
`service` publishes with `celery.current_app.send_task(
"autune.intelligence.on_context_completed", args=[links.model_dump(mode="json")])`.

The intended longer-term mechanism is a thin `autune_core.publish_event(name,
payload)` helper that owns the `contract.model_dump → send_task` convention for
every module; it touches `packages/core`, so it needs team approval. When it
lands, the one `send_task` call here moves behind it.

## Slack surface

- **Topic-link notice** — "이 안건은 2026년 9월 4일 회의에서 논의된 적 있습니다",
  with a link to the minutes. Capped at `AUTUNE_CONTEXT_MAX_TOPIC_LINK_NOTICES`
  per meeting (default 3); anything past the cap collapses into one rollup
  notice instead of one message per topic.
- **Decision-drift warning** — when a decision changed while a key stakeholder
  was absent. States the changing meeting's own date ("2026년 9월 4일
  회의에서..."), not just that it "changed" — a late-arriving lineage
  (fallback-then-late-lineage, or a backfilled recording) otherwise reads as
  "just now" regardless of when the meeting actually happened. Omits the date
  when the meeting has none set (see PR #263's discussion, issue #257).
- **Pre-meeting brief** — `AUTUNE_CONTEXT_BRIEF_LEAD_MINUTES` (default 10)
  before a scheduled meeting, to the team channel. The past meeting's topic
  labels and decision statements, and the issues this meeting takes up; never
  transcript excerpts. See "Pre-meeting brief".

Handlers acknowledge and delegate to `service`; no business logic in `slack.py`.

## Metric

Topic linking accuracy — **0.75+ at six weeks**, 0.85+ at three months.

```bash
uv run --package autune-context python -m autune_context.eval
```

The evaluation set is small, hand-labeled, and versioned inside the module. The
non-LLM extraction path keeps the metric deterministic. Link dismissals from the
confirmation flow feed threshold tuning.

Each suite's cases carry a `category` (what the case is testing — a paraphrase,
a shared keyword with a different meaning, a reversed vs. a modified decision),
and the report breaks accuracy down by it, prints a 95% Wilson interval next to
every headline number, and sweeps its threshold (`link_similarity_threshold`,
`lineage_match_threshold`) from the scores the run already stored. Topic
linking also reports link-level precision and recall and how often a no-link
meeting got an asserted link; decision lineage scores threading and change-type
classification separately, with a confusion matrix for the latter. What each
change type means for labeling is in `eval/decision_lineage/dataset.py`.

Each suite has a development set (`*_v2.json`, the default) and held-out sets
(`*_heldout_v*.json`), run with `--dataset` (`python -m autune_context.eval
topic-linking --dataset topic_linking_heldout_v1.json`). A held-out set stays
held out only until a rule or threshold is chosen by looking at its failures —
the PR that does that says so. Every set was written by the same person, so a
held-out set guards against overfitting to particular cases; it is not a sample
of real meetings.

`*_heldout_v1.json` has since been used to choose rules (`pipeline/change.py`,
`link_similarity_threshold`), so it is no longer held out. `*_heldout_v2.json`
was written before those rules and run once after them; the next rule or
threshold change needs a new held-out set — ideally masked real meetings.

To run it on a laptop without the team's inference endpoints, use the
`*_local` implementations (the `local-models` extra) and point
`AUTUNE_CONTEXT_NLI_LOCAL_MODEL` at the fine-tuned checkpoint.

## Privacy notes

- Retention interacts directly with this module: a linked past meeting may be
  deleted by the retention sweep. Handle a dangling link gracefully — show that
  the meeting is gone, never resurrect its content from an embedding.
- Embeddings are derived from masked text and are rows that cascade from
  `meetings.id`; an embedding outliving its meeting is a retention violation.
- The three self-hosted models receive masked transcript text within our
  infrastructure. The Phase 2 LLM path goes through `autune_integrations` so
  `check_outbound` runs; it receives only the snippet a feature needs — never a
  full transcript — and nothing goes into an exception message or a log line.
  See `../architecture/privacy.md` sections 2 and 6.
- No screen, endpoint, export, or Slack message in this module surfaces any
  per-person speaking ratio. This module does not compute one.
- `ctx_decision_versions.key_stakeholders_absent` records who was *not* present
  when a decision changed. Attendance is already shared data (`participants`),
  so this is a precomputation, not a new disclosure — but only inside the
  channels that already know who is asking: the `ContextLinks` event to E and
  the decision-drift Slack DM to the absent person themself, via
  `SlackClient.send_personal`. **`DecisionVersionRead` (the `GET
  /api/context/decisions*` read API) does not carry this field.** No route
  under `/api/context` checks the requester's team membership yet (#156), and
  a thread's whole point is spanning meetings, so an unauthenticated GET would
  let anyone walk every thread and build exactly the per-person aggregate this
  module has otherwise avoided ("how often is X absent from decisions"). Raised
  by the PR #90 reviewers, thought settled for #144, reopened once #204 (S22)
  showed the field rendered on screen from an unauthenticated route — see
  #188. Re-add it to the read API once #156 ships route auth; nothing else
  about the field changes.

## Phased delivery

| Phase | Scope |
| --- | --- |
| **0** | Lock the model stack and embedding dimension. Define the self-hosted serving contract with `infra/`. Propose `autune_core.publish_event` in Slack. Land this document. |
| **1** | Five migrations (`ctx_embeddings`, `ctx_topic_links`, `ctx_decisions`, `ctx_decision_versions`, `ctx_meeting_status`). `models.py`, `config.py`, manifest dependencies. `pipeline/` skeleton: the four Protocols, `Fake*` implementations, `registry.py` with the dimension guard, the warm-up hook. Tests: migration round-trip, meeting-deletion cascade, orphan-thread sweep. |
| **2** | `HybridRetriever` (KURE dense + BM25, RRF), `topics.py` (TextTiling + kiwipiepy labels). `service.run_topic_linking`, `publish_if_ready` (B-timeout path), the `send_task` publish. `on_transcript_ready` / `on_extraction_completed` wired. Follow-up: the evaluation harness and the first accuracy number. |
| **3** | `KlueKorNliHttp` and the KorNLI fine-tuning job. Wire `on_extraction_completed`: thread matching, NLI, version records, absent-stakeholder detection. Publish once both halves are in. |
| **4** | The four API routes (recursive-CTE lineage, confirmation flow). Frontend: S22 lineage timeline, S15 context tab, link-confirmation UI. |
| **5** | Slack notice and drift warning. Threshold tuning from dismissals. Push the metric to 0.75+. |

**Out of the six-week scope:** `ctx_materials`, material analysis, agenda
generation (S08), the S22 relationship graph, and the self-hosted LLM. The
pre-meeting brief was added to the build on 2026-09-29.

## Open questions

- How the brief's agenda reaches D from B's Jira integration: a contract
  carrying an issue's title, key, status and URL, either per scheduled meeting
  or as a team snapshot D keeps in its own table (#436). Until it lands, briefs go out
  with an empty agenda section.
- `send_brief` runs on `cpu_heavy` because the previous-meeting choice may
  embed and re-rank; its Slack post shares that worker. Moving the post onto
  `default` is a `TASK_ROUTES` line in `packages/core` plus a second task.
- The thresholds for asserting a link versus asking the user
  (`link_similarity_threshold = 0.74`, `link_confidence_threshold = 0.6`) come
  from short, synthetic evaluation meetings, with a thin margin between same-
  and different-topic pairs. They need re-checking against real meetings.
- Whether `autune_core.publish_event` lands before Phase 2, or D ships the
  interim `current_app.send_task` path.
- Whether the LLM client moves to `packages/integrations` at the start of
  Phase 2 (depends on B's and C's needs).
