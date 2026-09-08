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
   constraint is breaking: announce in Slack, get approval from every affected
   module owner, bump `CONTRACT_VERSION`, and ship it in one pull request.
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
| `ExtractionResult` | B | E | `autune.extraction.completed` |
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
- `source` is `"file_upload"` in the MVP. `"desktop_app"` arrives in Phase 2.

### 2. `ExtractionResult` — B → E

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
      "status": "open",
      "confidence": 0.88,
      "external_refs": [
        {"system": "notion", "url": "https://..."},
        {"system": "jira", "url": "https://..."}
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
      "spoke": ["user_001", "user_002"],
      "silent": ["user_003"]
    }
  ]
}
```

`severity` is one of `high`, `medium`, `low`. Only `high` is surfaced by
default in the UI.

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
      "decision_id": "dec_014",
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

- Additive change → bump the minor version (`1.0` → `1.1`)
- Breaking change → bump the major version (`1.1` → `2.0`), and only after Slack
  announcement plus approval from every affected owner

Consumers validate the major version and reject a mismatch loudly rather than
guessing.

## TypeScript generation

```bash
pnpm run gen:contracts
```

Generates `packages/contracts/ts/` from the Pydantic models. Never edit the
generated output. Regenerate and commit whenever the Python models change; CI
fails if the committed output is out of date.

## Adding a field — the checklist

1. Is it optional with a default? If not, it is a breaking change.
2. Does it contain personal data? If yes, stop and read `privacy.md`.
3. Bump the version.
4. Regenerate the TypeScript types and commit them.
5. Update the example payload in this document.
6. Tag every affected module owner on the pull request.
