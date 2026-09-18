# Glossary

Shared vocabulary. Use these terms — in code, in documents, in the UI, in Slack.
When a Korean term is given, it is the wording used in the product UI; the code
identifier is always the English term.

## Core entities

**Meeting (회의)**
One analysis unit: a single uploaded recording plus everything derived from it.
Identified by `meeting_id`. Owned by module A, defined in `packages/core`.

**Participant (참석자)**
A person present at a meeting. Distinct from `User`: a participant may be
unidentified ("Speaker 2") and never resolve to a user account.

**Utterance (발화)**
One continuous stretch of speech by one speaker, with a start time, an end
time, PII-masked text, and a confidence score. The atomic unit every downstream
module consumes. Owned by module A.

**Speaker (화자)**
The voice-level identity produced by diarization, before it is matched to a
person. Rendered as "Speaker 1", "Speaker 2" until identification succeeds.

**Speaker identification (화자 식별)**
Matching a diarized speaker to a known `User` using speaker embeddings.
Distinct from **diarization** (화자 분리), which only separates voices from one
another without naming them.

**Transcript (전사)**
The ordered list of utterances for a meeting. The canonical A → B/C/D payload.

## Module B — extraction

**Utterance kind (발화 유형)**
The kinds B reports for an utterance:
`commitment`, `decision`, `open_question`, `concern`, `ambiguous`.

B's classifier also answers `none` — most of a meeting is none of these — and a
`none` utterance never leaves the module: it is absent from
`ExtractionResult.classifications` rather than listed with a sixth kind.

**Commitment (약속)**
Someone stated they will do something. The raw material of an action item.

**Decision (결정)**
The meeting settled a question. Decisions are what module D tracks over time.

**Open question (미해결 질문)**
A question raised and not answered within the meeting.

**Concern (우려)**
A stated risk or objection that was not resolved into a decision.

**Ambiguous agreement (모호한 동의)**
Assent too weak to treat as a commitment — "네, 될 것 같아요", "한번 볼게요".
Verified with NLI; when it stays ambiguous, the speaker gets a confirmation DM.

**Action item (액션아이템)**
A structured, trackable task: assignee, description, due date, source utterance,
status. Syncs to Notion and Jira.

## Module C — gap detection

**Gap (갭)**
Something a meeting should have covered and did not, found by comparing the
meeting's topic graph against a domain template. Not the same as an open
question: an open question was asked, a gap was never raised at all.

**Domain template (도메인 템플릿)**
The checklist of items a given kind of discussion is expected to settle — a
feature-planning meeting is expected to cover performance requirements, error
handling, rollout, and so on.

**Topic graph (토픽 그래프)**
The graph of entities and their relations extracted from a meeting, stored as
rows in PostgreSQL and loaded into NetworkX to compute centrality, which
identifies which topics carried the meeting. Built per meeting, never
accumulated — cross-meeting linking is module D's job.

**Participation matrix (참여도 매트릭스)**
Per topic and per participant: who spoke and who was silent. Feeds gap risk
scoring — a topic nobody from engineering spoke on is a risk signal.

**Risk score (리스크 점수)**
The severity assigned to a gap. Only HIGH is surfaced by default.

## Module D — context

**Topic linking (토픽 연결)**
Connecting a topic in the current meeting to the past meetings where it was
discussed. Hybrid retrieval (BM25 + Sentence-BERT) with cross-encoder
re-ranking.

**Decision lineage (결정 계보)**
The chain of versions a single decision passed through across meetings. Each
version records what changed, when, in which meeting, and who was present.

**Decision drift (결정 변경)**
A decision changing without being explicitly revisited. Detected with NLI
between the previous decision text and the current one.

**Pre-meeting brief (프리미팅 브리프)**
The digest sent 30 minutes before a meeting: incomplete actions, unresolved
gaps, decision-lineage summary. Phase 2.

**Material (자료)**
A document uploaded alongside a meeting — spec, PRD, previous minutes. Chunked
and embedded into a pgvector column for retrieval.

## Module E — intelligence

**Meeting quality score (회의 품질 점수)**
An A–F grade combining decision density, gap count, action-item completion, and
participation balance.

**Alignment heatmap (얼라인먼트 히트맵)**
Cross-role agreement measured pairwise — where PM and engineering diverge, for
instance.

**Speaking ratio (발언 비중)**
The share of a meeting one person spoke for. **Private to that person.** See
`../architecture/privacy.md` — this term always carries that constraint.

**Influence map (영향력 맵)**
Proposal adoption rate, decision dominance, interruption patterns. Phase 2.

## Privacy

**PII masking (개인정보 마스킹)**
Detecting and replacing personal data in transcript text before storage.
Phone numbers, emails, national ID numbers, account numbers.

**Raw audio (원본 음성)**
The uploaded recording file. Deleted immediately after transcription. Never
persisted, never logged, never forwarded.

**Retention window (보관 기간)**
How long analysis results live before automatic deletion. 90 days by default.

**Consent flow (동의 플로우)**
Notifying participants that recording has started and letting a non-consenting
participant's speech be excluded from analysis.

## Engineering

**Module (모듈)**
One of the five owned units under `modules/`. Exactly one owner. Never imports
another module.

**Contract (계약)**
A Pydantic model in `packages/contracts` describing a payload that crosses a
module boundary. Frozen after W1; additive changes only.

**Event (이벤트)**
A Celery message carrying a contract payload from one module to another. The
only runtime coupling permitted between modules.

**Shared entity (공통 엔티티)**
A table defined in `packages/core` — `User`, `Team`, `Meeting`, `Participant`,
`Utterance`. Written by A, read by everyone.

**Table prefix (테이블 접두사)**
The mandatory prefix on a module-owned table: `aud_`, `ext_`, `gap_`, `ctx_`,
`intel_`. `agent_` is proposed in #260 for the agent layer, which is not a
module.

## Agent layer (proposed — #260)

Not built. Vocabulary is listed here so the design discussion uses one set of
words. See `../architecture/agent-layer.md`.

**Work item (업무 항목)**
One unit of work being tracked — an action, a gap, an open question, a decision
or a risk — with a status that outlives the meeting it was born in. Stored in
`agent_work_items`. Work that never came from a meeting lands in the same table.

**Tool (툴)**
A module function the agent may call, declared in
`modules/<name>/src/autune_<name>/tools.py`. Its docstring says **when to use
it**, which is what the agent reads.

**Orchestrator (오케스트레이터)**
The loop that decides which tools to call for a given trigger, in what order,
and what to do with the answers.

**Trigger (트리거)**
A reason to wake up: a schedule, an event, a due `next_check_at`, or a person
asking.

**Escalation level (에스컬레이션 단계)**
How hard the agent is pushing a stalled item: 0 watch, 1 DM the owner, 2 raise
it on the next agenda, 3 report to the lead.

**Action level (행동 등급)**
How dangerous an action is, L0 to L3. L2 and above need a person's approval;
L3 is forbidden. `L0-ext` covers reads that send content outside the system.

**Confidence gate (신뢰도 게이트)**
Below 0.5, the agent asks a person instead of acting. The design's answer to
extractors that are not accurate yet.
