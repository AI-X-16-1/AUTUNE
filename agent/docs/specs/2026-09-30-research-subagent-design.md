# Research subagent — design

**Owner:** 김민경 (@mkkim68) · **Date:** 2026-09-30 · **Gate:** runs end to end
on one real meeting by 10/9 (`docs/architecture/agent-layer.md` section 14)

## 1. What it is for

`agent-layer.md` section 3.1: when a meeting raises a question or argues over
something nobody could confirm, Research gathers what the team already holds
about it into a short document and **proposes** sharing it. A person reads the
document and approves before anyone else sees it.

The demo it has to carry on 10/9: a real recording goes through A–E, the
pipeline's last event wakes Research, a document appears as a proposal, an
approver approves it, and the team sees it on the meeting page. That is the
"acts first, a person confirms" loop the mentor asked for.

### Decided with the owner on 2026-09-30

| Question | Decision |
| --- | --- |
| Where a person sees the result | As an L2 proposal on the approval screen (10/7), then in the app. The same code answers a chat request. |
| Who writes the document | Rules gather the material; an LLM writes the text (two calls). |
| How far "send" goes by 10/9 | Approval only. An approved document is shown in the app; nothing leaves Autune. Slack DM follows #478. |
| Where the document lives | Its own table, deleted when **any** meeting it quotes is deleted. |
| Who sees an approved document | Every member of the team. |

### Out of scope

Open-web search (section 13.3), uploaded material (there is no store for it),
module D's tools (D has no `tools.py` yet), Slack or e-mail delivery, and the
approval screen itself (plan mode, 10/7).

## 2. Deviation from `agent-layer.md`, and why

Section 3.1 wakes Research on `autune.transcript.ready`. That event fans out to
B, C, D and the agent layer at once, so when Research wakes, B has not
classified the meeting and `extraction.unresolved_questions` is empty. Research
wakes on **`autune.intelligence.completed`** instead — E publishes it after B,
C and D have finished. When E re-aggregates and publishes again, the trigger's
redelivery guard skips the second one (#509); the first result is enough for
Research. The doc change is PR ⑤.

## 3. Flow

Scope: the run's `team_id`, and meeting M. On the trigger M is the event's
meeting. On a chat request the scope has no meeting, and Research takes the
team's most recent meeting whose analysis finished (`audio.recent_meetings`).

| # | Node | Does | Calls |
| --- | --- | --- | --- |
| 1 | `read` | `audio.meeting_overview(M)`, `extraction.unresolved_questions(M)`. No questions → finish, `ok=True`, no proposal. | 2 tools |
| 2 | `terms` | LLM: one or two search terms per question, five in all, JSON out. | 1 LLM |
| 3 | `search` | `audio.search_team_meetings(term)` per term, M excluded. | ≤ 5 tools |
| 4 | `write` | LLM: questions + matches → Markdown in three parts: 제기된 질문 / 과거 회의에서 나온 것 / 아직 모르는 것. | 1 LLM |
| 5 | `save` | `agent.save_research_document(body, utterance_ids)`, then propose L2 `agent.share_research_document(document_id)`. | 1 tool |

At most 8 tool calls against the run's 15 on the trigger path; a chat run spends
two more finding its meeting (a refused `meeting_overview`, then
`recent_meetings`), so at most 10. Every node that fails ends the run
with `ok=False` and no proposal; a search that finds nothing is not a failure,
and the document says so under 아직 모르는 것.

### The two LLM calls

Both go through `autune_integrations` `HttpClient` → `check_outbound`, like
`GeminiRouter` (#449). What leaves is masked text read from the database: B's
question sentences and A's matched utterances. The meeting-derived part of each
prompt is capped at `MAX_OUTBOUND_CHARS` (4,000; section 9), cut by dropping
the lowest-ranked matches first. #392's condition applies as it does to the
router: until a paid key, demo meetings only.

B marks `unresolved_questions` as unreviewed labels that must not be posted on
the agent's own authority (#261 rule 3). They are not: the document is shown to
nobody but the approver until the approver has read and approved it, which is
the review.

## 4. New pieces

### ① A tool: `audio.search_team_meetings(session, team_id, query, *, days=90, exclude_meeting_id=None)`

`find_utterances` searches one meeting, so five past meetings cost five calls.
This searches the team's meetings from the last `days` days at once and returns
the top five matches, newest meeting first, each with meeting title, date,
time, speaker and masked text (200 characters). Same rules as A's other tools
(#507): consented speakers only, the query matched as plain text, 100-character
query cap, the model's ids never echoed.

### ② Agent-layer tools and actions, tables, API

- `collect_tools` and `collect_actions` also collect the layer's own
  (`autune_agent.main.own_tools`), named `agent.<function>`. Modules still
  cannot import the layer; nothing changes for them.
- `agent.save_research_document(session, team_id, meeting_id, body, utterance_ids)`
  — a tool, L0 (section 8: a write to `agent_*`). Upserts the one `proposed`
  document for M; an `approved` or `rejected` document is never overwritten, and
  a new one is created instead. Returns the `rdoc_` id as evidence.
- `agent.share_research_document(session, team_id, document_id, decided_by)`
  — an action, L2. Marks the document `approved`. Plan mode runs it after
  approval; rejection is plan mode's record.
- `GET /api/agent/research?team_id=&meeting_id=` — approved documents for any
  team member; `proposed` ones too for an approver with scope `research` or `any`.

### ③ The subgraph

`autune_agent/subagents/research/`: `graph.py` (five nodes), `writer.py` (the
two LLM calls behind a small protocol, so tests use a fake), `__init__.py`
exporting `SUBAGENT` with `tools` = the five registry names and
`triggers = (INTELLIGENCE_COMPLETED,)`.

### ④ The research card

On the stored meeting screen (`apps/web/src/features/transcript/`), above the
transcript: approved documents only, rendered as Markdown, hidden when there
are none.

## 5. Storage and deletion

```
agent_research_documents
  id           text PK            rdoc_…
  team_id      → teams     ON DELETE CASCADE
  meeting_id   → meetings  ON DELETE CASCADE      M
  body         text                masked text only
  status       proposed | approved | rejected
  run_id       → agent_runs ON DELETE SET NULL
  decided_by   → users     ON DELETE SET NULL
  created_at, decided_at

agent_research_sources
  document_id  → agent_research_documents ON DELETE CASCADE
  meeting_id   → meetings  ON DELETE CASCADE
  PRIMARY KEY (document_id, meeting_id)
```

**A document goes when any meeting it quotes goes.** A foreign key can cascade
from one parent only, so an `AFTER DELETE` trigger on `agent_research_sources`
deletes the parent document when a source row is deleted. The retention sweep
deletes meetings, so the document's lifetime is its earliest-expiring source's.

**The sources are computed, not trusted.** `save_research_document` receives
utterance ids and looks up their meetings itself, and always adds M. A model
that leaves a source out cannot open a gap in the deletion path.

**`agent_runs` stays free of text**, as settled on #509: the save tool's
evidence is the `rdoc_` id, and the L2 proposal's arguments are
`{"document_id": …}`. The approval screen and the card read the table.

No speaking ratio: the document quotes who said what and when, never how much
anyone spoke (invariant 11, #361).

## 6. Testing

- **Subgraph** (unit, `mock_tool` and a fake writer): no questions → no
  proposal; ≤ 8 tool calls; meeting-derived prompt ≤ 4,000 characters; exactly
  one L2 proposal whose arguments are only `document_id`; no matches still
  writes a document; a writer failure ends `ok=False` with no proposal; a chat
  run picks the most recent analysed meeting.
- **`search_team_meetings`** (PostgreSQL): another team's meetings never appear;
  non-consented speakers never appear; M is excluded; five results, 200
  characters each.
- **Tables and tools** (PostgreSQL): sources computed from utterance ids with M
  always present; an approved document is not overwritten; **deleting any one
  source meeting deletes the document**; `GET /research` shows `proposed` only
  to a research approver.
- **End to end** (10/6–10/8): one real recording through A–E; the document is
  `proposed`; approving it through the API before the approval screen exists
  shows it on the card.

## 7. Delivery

All after #509 merges, except ①.

| PR | Content | Approvals |
| --- | --- | --- |
| ① | `audio.search_team_meetings` | 1 (module A) |
| ② | own tools/actions collection, two tables and trigger, save tool, share action, `GET /research` | 1 (`agent/` only) |
| ③ | Research subgraph and `SUBAGENT`, this spec | 1 |
| ④ | research card, plus an `agent` entry in the shared API client | 5 (`apps/web/src/shared`) |
| ⑤ | `agent-layer.md`: Research wakes on `intelligence.completed`; reads what we hold | 5 (`docs/`) |

Schedule: ①② 10/1–10/2 · ③ 10/3–10/4 · ④⑤ 10/5 · end to end 10/6–10/8.
