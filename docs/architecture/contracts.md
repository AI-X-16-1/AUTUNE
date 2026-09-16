# Inter-Module Contracts

`packages/contracts` defines every payload that crosses a module boundary.
It is the single most consequential package in the repository: four modules
break at once when it changes badly.

## Rules

1. **Pydantic models only.** No behavior, no I/O, no database access, no
   imports from `modules/*` or from `autune_core`.
2. **Frozen after W1. Additive changes only.** Adding an optional field with a
   default is safe and needs a normal review. Removing a field, renaming a
   field, changing a type, making an optional field required, or narrowing a
   constraint is breaking: open an issue with the `decision` label, get approval
   from every affected module owner, bump `CONTRACT_VERSION`, and ship it in one
   pull request.
3. **Every payload carries `contract_version` and `meeting_id`.**
4. **Timestamps are UTC ISO-8601.** Audio offsets are seconds as floats,
   relative to the start of the recording.
5. **IDs are prefixed strings**, not bare UUIDs: `mtg_…`, `utt_…`, `act_…`,
   `gap_…`, `dec_…`, `user_…`.
6. **Never put unmasked text in a contract.** By the time a payload leaves
   module A, PII masking has already been applied. See `privacy.md`.
7. **TypeScript types are generated, never hand-written.** The Pydantic models
   are the source of truth; `apps/web` consumes generated types.

CODEOWNERS requires approval from the whole team for changes to this package.

## The five payloads

| Contract | From | To | Event |
| --- | --- | --- | --- |
| `TranscriptReady` | A | B, C, D | `autune.transcript.ready` |
| `ExtractionResult` | B | D, E | `autune.extraction.completed` |
| `GapReport` | C | E | `autune.gap.completed` |
| `ContextLinks` | D | E | `autune.context.completed` |
| `IntelligenceSnapshot` | E | apps (dashboard, Slack) | `autune.intelligence.completed` |

### 1. `TranscriptReady` — A → B, C, D

The canonical meeting record. Everything downstream is derived from it.

```json
{
  "contract_version": "1.0",
  "meeting_id": "mtg_20260918_001",
  "utterances": [
    {
      "id": "utt_001",
      "speaker": "김서연",
      "speaker_id": "user_001",
      "role": "PM",
      "start": 0.0,
      "end": 3.5,
      "text": "검색 개인화 기능 이번 스프린트에서 진행하겠습니다",
      "confidence": 0.94
    }
  ],
  "metadata": {
    "duration": 2700,
    "participants": ["김서연", "이개발", "최디자인", "정데이터"],
    "source": "file_upload",
    "language": "ko",
    "privacy": {
      "original_audio_deleted": true,
      "pii_masked": true
    }
  }
}
```

Notes for consumers:
- `speaker_id` is `null` when the speaker was diarized but not identified.
  `speaker` then holds a display label such as `"Speaker 2"`. Handle this case —
  it is common.
- `role` is `null` when the speaker is unidentified or has no role on record.
- `text` is already PII-masked. Do not attempt to recover the original.
- `privacy.original_audio_deleted` must be `true`. If it is not, the pipeline
  is broken: fail loudly, do not proceed.
- `source` is `"file_upload"` or `"web_mic"` — both are MVP and both produce
  the same payload, so consumers need not branch on it. `"desktop_app"`
  arrives in Phase 2.

### 2. `ExtractionResult` — B → D, E

D consumes this too, for `decisions` only. See "The B → D boundary" below.

```json
{
  "contract_version": "1.0",
  "meeting_id": "mtg_20260918_001",
  "action_items": [
    {
      "id": "act_001",
      "description": "검색 개인화 스펙 문서 작성",
      "assignee_id": "user_001",
      "assignee_label": "김서연",
      "due_date": "2026-09-25",
      "source_utterance_ids": ["utt_001"],
      "status": "todo",
      "confidence": 0.88,
      "external_refs": [
        {"system": "notion", "url": "https://..."},
        {"system": "jira", "url": "https://..."}
      ]
    }
  ],
  "decisions": [
    {
      "id": "dec_014",
      "statement": "검색 정렬은 인기순으로 진행",
      "source_utterance_ids": ["utt_001", "utt_002"],
      "confidence": 0.86,
      "stance_by_role": [
        {"role": "Dev", "identified": 4, "supporting": 3, "concerns": 1}
      ]
    }
  ],
  "classifications": [
    {
      "utterance_id": "utt_001",
      "kind": "commitment",
      "confidence": 0.91,
      "nli_verified": true
    }
  ],
  "ambiguous_agreements": [
    {
      "utterance_id": "utt_042",
      "reason": "weak_assent",
      "confirmation_sent": true
    }
  ]
}
```

`kind` is one of `commitment`, `decision`, `open_question`, `concern`,
`ambiguous`.

A `Decision` is not the same as a `Classification` with `kind="decision"`. The
classification marks one utterance; a decision is an entity that often spans
several, and it is what D keys a lineage on.

`stance_by_role` counts people per role, never per person (#168). A role is
listed only when the meeting had at least `STANCE_MIN_IDENTIFIED_PER_ROLE` (3)
identified people in it, and `identified` carries that number so the model
rejects a row below the gate. People are counted by distinct `user_id`; a person
who spoke without backing the decision or raising a concern is in neither
count, so the field says nothing about who spoke. An empty list means either
that no role cleared the gate or that stance was not computed — do not tell the
two apart. It stays empty until the Korean classifier has a measured quality
(#10); E shows "not enough data" until then.

`status` is one of `needs_confirmation`, `todo`, `in_progress`, `done` — the
four columns of the action board (S17) and the Jira states they map to.
`needs_confirmation` means Autune has the item but no external issue exists yet.

### 3. `GapReport` — C → E

```json
{
  "contract_version": "1.0",
  "meeting_id": "mtg_20260918_001",
  "gaps": [
    {
      "id": "gap_001",
      "category": "technical_spec",
      "title": "Performance requirements not defined",
      "severity": "high",
      "risk_score": 0.82,
      "template_item": "nfr.performance",
      "related_topic_ids": ["topic_003"],
      "suggested_question": "검색 개인화의 목표 응답 시간은 몇 ms인가요?"
    }
  ],
  "topics": [
    {
      "id": "topic_003",
      "label": "search personalization",
      "centrality": 0.71,
      "utterance_ids": ["utt_001", "utt_017"]
    }
  ],
  "participation": [
    {
      "topic_id": "topic_003",
      "spoke": ["prt_001", "prt_002"],
      "silent": ["prt_003"]
    }
  ]
}
```

`severity` is one of `high`, `medium`, `low`. Only `high` is surfaced by
default in the UI.

`spoke` and `silent` hold **participant ids** (`prt_…`), not user ids. An
unidentified speaker has a participant row and no user id, and silence is the
evidence a gap is raised on, so a user-id list would drop exactly the people a
participation gap is about. A participant id is scoped to one meeting, so the
payload alone carries no cross-meeting identity. Any module can still resolve it
to a user through the shared `participants` table, so building a per-person
record of silences across meetings is a `privacy.md` section 3 violation on the
consumer's side — the id format does not prevent it.
When diarization splits one person into several participant rows, C reports
them once, under the smallest of their participant ids. See
`../modules/gap.md`, "Step 9 as built".

### 4. `ContextLinks` — D → E

```json
{
  "contract_version": "1.0",
  "meeting_id": "mtg_20260918_001",
  "topic_links": [
    {
      "topic_label": "search personalization",
      "linked_meeting_id": "mtg_20260904_002",
      "linked_meeting_date": "2026-09-04",
      "similarity": 0.83,
      "rerank_score": 0.91
    }
  ],
  "decision_lineage": [
    {
      "thread_id": "thr_007",
      "source_decision_id": "dec_014",
      "current_statement": "인기순 정렬로 진행",
      "previous_statement": "실시간 개인화로 진행",
      "previous_meeting_id": "mtg_20260904_002",
      "change_type": "modified",
      "nli_label": "contradiction",
      "confidence": 0.87,
      "key_stakeholders_absent": ["user_002"]
    }
  ]
}
```

`change_type` is one of `unchanged`, `modified`, `reversed`, `new`.

## The B → D boundary

This is the only place two module owners depend on each other, so it is written
down rather than left to be discovered.

**B owns: what counts as a decision in this meeting.** It publishes `decisions`,
each with a `dec_` id, the statement as settled, and the utterances it came
from. B does not know or care whether the decision existed before.

**D owns: whether this decision is the same one as before.** It matches each of
B's decisions into a lineage thread with its own `thr_` id, then compares
statements with NLI to classify the change.

Two identities meet in `DecisionChange` and they are not interchangeable:

| Field | Owner | Lifetime |
| --- | --- | --- |
| `thread_id` (`thr_`) | D | Spans meetings — the lineage's identity |
| `source_decision_id` (`dec_`) | B | This meeting only |

**D must not extract decisions itself.** Doing so would duplicate B's
classifier, and the two would disagree — a decision would appear in the summary
tab (S15) and be missing from the lineage view (S22), which reads to a user as a
bug.

**A failure in B must not cost the user their topic links.** Topic linking needs
only the transcript, so it runs in parallel with B and C; decision lineage needs
B, so it runs after. D publishes `ContextLinks` once both are in, or with an
empty `decision_lineage` and `"extraction"` in `missing_sources` when B never
reports.

### 5. `IntelligenceSnapshot` — E → apps

```json
{
  "contract_version": "1.0",
  "meeting_id": "mtg_20260918_001",
  "team_id": "team_001",
  "quality_score": {"grade": "B", "value": 0.74},
  "gap_distribution": {"technical_spec": 3, "ownership": 1},
  "alignment": [
    {"role_a": "PM", "role_b": "Dev", "score": 0.62}
  ],
  "predictions": [
    {"kind": "misalignment_risk", "horizon_days": 14, "probability": 0.31}
  ]
}
```

**Speaking ratios are not in this contract, and never will be.** They are
computed and delivered directly to the individual speaker; they are not stored
in aggregate and not exposed through any shared payload. See `privacy.md`.

## Versioning

`packages/contracts/__init__.py` exports `CONTRACT_VERSION`. Every payload
carries it.

- Additive change → bump the minor version (`2.0` → `2.1`)
- Breaking change → bump the major version (`2.0` → `3.0`), and only after a
  `decision` issue plus approval from every affected owner

Version 2.0 renamed `DecisionChange.decision_id` to `thread_id` and added
`source_decision_id`, because one field was carrying two identities. It was done
before anyone consumed the field, which is the only cheap moment for a change
like that.

Consumers validate the major version and reject a mismatch loudly rather than
guessing.

## Fixtures

`autune_contracts.fixtures` ships the payloads every module tests against:

```python
from autune_contracts import TranscriptReady, fixtures

transcript = TranscriptReady.model_validate(fixtures.load("transcript_ready.unidentified"))
```

Available: `transcript_ready.short`, `transcript_ready.typical`,
`transcript_ready.unidentified`, `extraction_result`, `gap_report`,
`context_links`, `intelligence_snapshot`.

`transcript_ready.unidentified` exists because every consumer must handle a null
`speaker_id`, and it is the case people forget. Fixtures hold synthetic text
only — never commit a real transcript, even masked.

## Consuming a payload safely

```python
from autune_contracts import TranscriptReady, validate_major_version

transcript = TranscriptReady.model_validate(payload)
validate_major_version(transcript)  # reject an incompatible producer
transcript.require_privacy_guarantees()  # refuse unmasked or undeleted input
```

`require_privacy_guarantees()` raises when module A published without deleting
the raw audio or without masking. Call it before touching `utterances`.

## TypeScript generation

```bash
pnpm run gen:contracts
```

Generates `packages/contracts/ts/schema.json` and `index.d.ts` from the Pydantic
models. Never edit the generated output. Regenerate and commit whenever the
Python models change; CI fails if the committed output is out of date.

## Adding a field — the checklist

1. Is it optional with a default? If not, it is a breaking change.
2. Does it contain personal data? If yes, stop and read `privacy.md`.
3. Bump the version.
4. Regenerate the TypeScript types and commit them.
5. Update the example payload in this document.
6. Tag every affected module owner on the pull request.
