# Autune — Product Requirements (Engineering View)

This is the engineering-facing summary of the product plan. Pricing, go-to-market,
market sizing, and revenue projections are deliberately omitted — they do not
affect implementation decisions. Ask the product owner if you need them.

---

## 1. Problem

Cross-functional meetings waste money in three specific ways.

**Nothing gets organized.** Commitments made out loud ("I'll upload it by next
week") get buried in notes. The most junior person spends 30–60 minutes writing
the meeting up and still misses things. The next meeting opens with "did that
get done?"

**Things are missed silently.** A team decides to "personalize search" without
anyone asking about performance requirements or cold-start handling. Two weeks
later, implementation stalls on decisions nobody made.

**Context breaks between meetings.** The same topic is discussed three weeks in
a row and nobody remembers what was decided. An agreement on "real-time
personalization" quietly becomes "sort by popularity" and nobody knows when or
by whom.

Existing tools (Otter.ai, Clova Note, Fireflies.ai, Notion AI) all stop at
"this meeting": transcription plus summary. Cross-meeting linking, decision
tracking, and gap detection are entirely manual.

## 2. Product

> One meeting recording in — automatic tracking of who committed to what, plus
> the discussions that were missed.

Autune covers the full meeting lifecycle:

| Phase | What Autune does |
| --- | --- |
| Before | Analyze uploaded material → draft agenda → pre-meeting brief (Phase 2) |
| During | Real-time transcription, interim summaries, undecided-item alerts (Phase 2) |
| After | Action extraction and tracking, gap detection, context linking, personal speaking-ratio feedback, Slack/Notion/Jira delivery |
| Over time | Decision lineage, dashboard, influence map, topic linking |

## 3. Users

**Organizations:** 50–500 person IT/tech companies whose cross-functional teams
meet three or more times a week.

**Adoption order:** PM (meeting write-up automation) → engineering lead (gap
reports) → VP/CTO (dashboard).

| Persona | Role | Pain | What Autune gives them |
| --- | --- | --- | --- |
| Junior PM (26) | Default note-taker for the squad | "Writing up meetings isn't my job, but it takes me an hour every time." | Upload the recording; minutes and action items are automatic |
| Backend lead (35) | Meets PM and design 3×/week | "I thought we agreed on everything, then I start coding and half of it is undefined." | A gap report right after the meeting: "technical specification not defined" |
| Engineering VP (40) | Manages four squads | "I want to see which teams communicate well as a number." | Per-team communication health score, cross-role gap heatmap |

## 4. Modules

| Module | Owner | Responsibility | What the user gets |
| --- | --- | --- | --- |
| **A. Audio Pipeline** | 김민경 | Recording upload → STT → speaker diarization and identification → PII masking → raw audio deletion → unified transcript format | An accurate record of who said what |
| **B. Structured Extraction** | 강민구 | Classify utterances into five kinds, build action-item cards, verify ambiguous agreement with NLI, sync to Notion/Jira, generate role-specific reports | Automatically organized action-item cards |
| **C. Gap Detection** | 박재경 | Entity and relation extraction → topic graph → participation matrix → domain-template comparison → risk scoring | A list of what this meeting missed |
| **D. Meeting Context Engine** | 문민재 | Material analysis → agenda generation, past-topic retrieval and linking, decision lineage tracking, pre-meeting briefs | "We decided this last time" |
| **E. Meeting Intelligence** | 이승환 | Quality scoring, gap classification, prediction, heatmaps, weekly reports, influence map, personal speaking-ratio DMs | A team communication dashboard |

## 5. Key features

### 5.1 Audio capture and per-speaker transcription (A)
- Input: uploaded recording file. Desktop app is Phase 2.
- Whisper STT + Pyannote diarization + speaker embedding identification.
- Real-time transcription with timestamps. Speakers start as "Speaker 1" and
  switch to a name once identified.
- Interim summaries at fixed intervals.
- When an end time is registered, remind participants about undecided items
  (Phase 2).

### 5.2 Action item extraction and tracking (B)
- Five-way utterance classification: commitment / decision / open question /
  concern / ambiguous expression.
- NLI verification of ambiguous agreement; weak agreement triggers a Slack DM
  asking the speaker to confirm.
- Automatic Notion and Jira issue creation with assignee mapping and due-date
  parsing.
- Role-specific reports delivered to Slack.
- Incomplete items from previous meetings resurface in the next one.

### 5.3 Cross-functional gap detection (C)
- Automatic topic graph construction from entity and relation extraction.
- Participation matrix: who spoke and who stayed silent, per topic.
- Domain-template comparison finds missing items; each gap gets a risk score.
- Concrete questions are generated to close each gap.

### 5.4 Meeting context engine (D)
- **Agenda from material:** analyze documents uploaded before the meeting and
  draft a discussion agenda (Phase 2).
- **Topic linking:** when a topic appears that was discussed before, link it
  automatically — "this came up in the meeting on 2026-09-04" with a link.
- **Decision lineage:** track how a single decision mutated across meetings as a
  timeline; warn when it changed while a key stakeholder was absent.
- **Pre-meeting brief:** 30 minutes before a meeting, send incomplete actions,
  unresolved gaps, and a decision-lineage summary to Slack (Phase 2).

### 5.5 Intelligence dashboard (E)
- Meeting quality score (A–F) trend.
- Cross-role alignment heatmap.
- Gap-type distribution over time.
- Misalignment prediction.
- Influence map: proposal adoption rate, decision dominance, interruption
  patterns (Phase 2).
- Automatic weekly insight report.

### 5.6 Personal speaking-ratio feedback (E)
- After a meeting, each participant receives **their own** speaking ratio by
  Slack DM.
- Nobody — including administrators — can see anyone else's ratio.
- Aggregate speaking-ratio records are not stored.
- Purpose is self-calibration, not measurement. This is a hard product
  constraint; see `../architecture/privacy.md`.

### 5.7 Proactive agent (Phase 2)
- Meeting-room discovery and booking through the company system or Microsoft
  Teams API; learn occupancy patterns and recommend open slots.
- Predict that a meeting is needed — when unresolved gaps pass a threshold or
  action items approach their deadline — then propose a time from participants'
  calendars and book it.

## 6. Privacy and legal compliance

Meeting recordings contain sensitive personal data. These constraints are
applied at design time, not bolted on:

1. **No raw retention** — the recording file is deleted immediately after
   analysis. Only derived results are stored; the original audio is
   unrecoverable.
2. **Automatic PII masking** — phone numbers, emails, national ID numbers, and
   account numbers are detected in the transcript and masked before storage
   (`010-****-5678`). The unmasked text is never written to the database.
3. **Speaking ratio is private to the speaker** — see 5.6.
4. **Retention and deletion** — results are kept 90 days by default (adjustable
   per team) and deleted automatically afterwards. Users can delete their data
   at any time. Leaving a team deletes that user's utterance data.
5. **Participant consent** — everyone is notified when recording starts, and
   there is an option to exclude a non-consenting participant's speech from
   analysis.

Implementation rules: `../architecture/privacy.md`.

## 7. Architecture summary

```
[Input]
  Recording file upload ───┐
  Material upload (PDF) ───┤
  Desktop app (Phase 2) ───┘
              │
              ▼
┌───────────────────────────────────────────────────────┐
│  [A] Audio Pipeline                                   │
│  Whisper STT + Pyannote diarization + speaker embed   │
│  → PII masking → raw audio deleted immediately        │
│  → unified format: { speaker, timestamp, text }       │
└───────────────────────┬───────────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
    ┌──────────┐  ┌──────────┐  ┌──────────┐
    │   [B]    │  │   [C]    │  │   [D]    │  ← parallel
    │Extraction│  │   Gap    │  │ Context  │
    └────┬─────┘  └────┬─────┘  └────┬─────┘
         ├→ Notion/Jira│             │
         ├→ Slack      ├→ Slack      ├→ Slack
         └─────────────┼─────────────┘
                       │ event log
                       ▼
                 ┌──────────┐
                 │   [E]    │
                 │Intellig. │──→ Slack
                 └──────────┘
```

Detail: `../architecture/async-pipeline.md`.

## 8. AI stack per module

| Module | Non-LLM core | LLM role | Non-LLM share |
| --- | --- | --- | --- |
| A | Whisper, Pyannote, speaker embedding, silero-vad, PII detection | Interim summary generation | ~80% |
| B | DeBERTa fine-tuned classifier, NLI verification | Reference resolution, report generation | ~60% |
| C | spaCy NER, relation extraction, Neo4j, PageRank / betweenness centrality | Relation extraction assistance | ~70% |
| D | Sentence-BERT, BM25, cross-encoder re-ranking, NLI for decision-change detection | Agenda and brief generation | ~70% |
| E | SetFit, XGBoost, Prophet, active learning | Report generation | ~75% |

The non-LLM share is a design target, not a metric we measure. It exists to
keep the team building real models rather than prompt chains.

## 9. Technology choices

| Area | Technology | Why |
| --- | --- | --- |
| Backend | FastAPI (Python 3.12) | AI library ecosystem, async |
| Frontend | Next.js + Tailwind (Node 22) | Fast iteration, responsive |
| Task queue | Celery + Redis | AI pipeline orchestration |
| Database | PostgreSQL | Structured data and history |
| Graph DB | Neo4j | Topic graph, decision lineage |
| Vector DB | Chroma | Embedding search, topic matching, material retrieval |
| Slack | Bolt for Python | Bot framework |
| External | Notion API, Jira REST API, Google Calendar API | Action item and schedule sync |
| Infra | Railway / AWS with GPU instances | STT inference |
| Desktop (Phase 2) | Electron | System audio capture |

## 10. Roadmap

| Week | Focus | Milestone |
| --- | --- | --- |
| W1 | Shared infrastructure (2 days): monorepo, DB, auth, Slack bot skeleton, API contracts. Individual PoC (3 days) | Each module's PoC runs |
| W2 | AI pipelines + API serving. Module A finishes first | Each module works through its API |
| W3 | Inter-module wiring + frontend start. Slack handlers. Notion/Jira integration begins | Recording → pipeline end to end |
| W4 | Core UI: live transcript, action board, gap report, context view | Usable by non-engineers |
| W5 | Dashboard + internal beta on 5–10 real meetings. Model tuning | Real-meeting E2E validation |
| W6 | Bug fixes, performance, landing page, demo video, pitch deck | Deployable MVP |

## 11. MVP scope

**In the 6-week MVP:** recording upload; STT + diarization + live transcript;
automatic PII masking; immediate raw-audio deletion; action item extraction and
tracking; Notion/Jira integration; gap detection; Slack integration; past-topic
linking; basic decision lineage; personal speaking-ratio DM; basic dashboard.

**Phase 2:** material upload → agenda generation; pre-meeting brief; influence
map; desktop app; role-specific summaries; in-meeting undecided-item alerts;
action automation; proactive agent (room booking, meeting-need prediction);
advanced dashboard and decision lineage.

Do not build Phase 2 features during the six weeks. If a Phase 2 feature seems
necessary to make an MVP feature work, that is a scoping conversation, not an
implementation decision.

## 12. Success metrics

### Product
| Metric | 6 weeks | 3 months |
| --- | --- | --- |
| Action item extraction F1 | 0.80+ | 0.88+ |
| Speaker diarization DER | ≤ 15% | ≤ 10% |
| Gap detection precision | 0.70+ | 0.82+ |
| Topic linking accuracy | 0.75+ | 0.85+ |
| PII masking recall | 0.95+ | 0.99+ |
| Processing time | ≤ 1.5× recording length | ≤ 1× |

### Business
| Metric | 6 weeks | 3 months |
| --- | --- | --- |
| Beta teams | 5 | 25 |
| Weekly active analyses | 15 | 100 |
| Free → paid conversion | — | 12% |
| NPS | 40+ | 50+ |

Each module owner is responsible for their own product metric and should be
able to report it on demand from an evaluation script in their module's
`tests/` or `eval/` directory.

## 13. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Korean STT accuracy | High | Domain hints + accumulated user corrections |
| Diarization errors | High | Voice enrollment + manual correction UI + confirmation DM |
| PII masking misses | High | Regex + NER double detection. User-reported misses are deleted immediately |
| Gap detection false positives | Medium | Threshold tuning from feedback. Only HIGH severity shown by default |
| Topic mis-linking | Medium | Cross-encoder re-ranking + user confirmation UI |
| GPU cost | Medium | whisper.cpp CPU inference + batch processing |
| Six weeks is not enough | Medium | Explicit MVP scope. Briefs and influence map are deprioritized |
| Privacy legal exposure | High | No raw retention + PII masking + retention limits + user deletion |

## 14. Differentiation

Existing tools: recording → text → done. "This meeting" only.

Autune covers the entire meeting lifecycle, links context across meetings, and
protects personal data by design. The moat is a data network effect: the more
meetings accumulate, the more accurate context linking, gap patterns, and
prediction become.
