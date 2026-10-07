# E agent — the Report subagent answers for all of module E

**Owner:** 이승환 (@lsh2217) · **Date:** 2026-10-05 · **Status:** Design, agreed
with the owner in chat and revised after a first review (same day); built
2026-10-06 (see "Changes during implementation" at the end)

## 1. What it is for

Every E feature, the dashboard's statistics included, should be reachable in
chat. Today a chat question about E takes one of two paths, and neither covers
E:

- "Write this meeting's report" is routed to the Report subagent. That
  subagent has no LLM: a fixed tool order and a template. It answers nothing
  else.
- Any other E question falls to the main agent's ask loop (#677). That loop
  calls three of E's read tools directly (`meeting_quality`, `team_trend`,
  `recurring_gaps`) and cannot act. The heatmap, predictions, meeting
  reports, weekly reports and the weekly schedule are out of its reach.

The target is the shape the owner described: the main agent sends anything
about E to E's agent, and E's agent reads or acts with all of E's tools.

```
chat ─▶ main agent ─(routes on the description)─▶ E agent ─▶ E's tools
```

### Decided with the owner on 2026-10-05

| Question | Decision |
| --- | --- |
| One agent or two | **One.** The Report subagent widens into the E agent. A second "insights" subagent would split "show / post / redo the report" between two routes. Adding E tools to the ask loop instead was rejected: it is read-only and lives in `main/`. |
| Its name | It keeps the registry name `report`. The approver scope `report` (`main/pending.py`, `SCOPES`) is keyed on it, so a rename would touch `main/` and every team's approver rows. It is called "the E agent" in prose and on screens. |
| First version | **Reads and actions.** Actions keep section 8's levels: L1 runs and the person is told after; L2 waits for an approver. |
| RAG | Yes, where it makes answers better: a **metric glossary** that the agent retrieves from, so a meaning or a formula is quoted, not made up (section 5). A search over report text is a later step (section 8). |
| A rejected approval | It leaves E's draft as it was. A person changes something on the dashboard to ask again, or asks the E agent in chat to propose the post again. |
| A post asked for outside a meeting | Proposed only from a run scoped to that meeting. From the team view the agent finds the meeting and links its screen (section 4, "Posting from the team view"). |
| The glossary's language | Korean, as user-facing response content (section 5, "Language"). |

### Out of scope

- Any LLM-written report body: a re-draft runs the template.
- Editing a report's text in chat: the dashboard card is the one place a person
  writes report text.
- Speaking ratios in any form (invariant 11).
- Charts drawn inside the chat (S34 is `main/`'s): answers are text with a
  link to the dashboard.
- LangChain `create_agent`. The team has not adopted it yet. The chat path is
  written so that `GeminiTools` can later sit behind a `create_agent` chat
  model (`main/toolcall.py` keeps that seam).

## 2. Deviations from `agent-layer.md`, and why

| `agent-layer.md` says | This design | Why |
| --- | --- | --- |
| 3.1: Report composes "from a template (no LLM)". | Template path unchanged. A chat request runs a Gemini tool loop. | Free questions need a model to choose tools; the report body still never comes from one. |
| 3.1: Report reads B, C, D. "Not E's scores." | The chat path also reads E's own tools. | The E agent's job is E. The template path's report still carries no quality grade. |
| 4 (E row): two actions for Report. | Adds one L1 action, `set_weekly_report_schedule`, and six reads. | The dashboard features the chat now reaches. |

Section 3.1's row and section 4's E row change in the same PR as the code.

## 3. The tool map

"Have" is a tool that exists today; "new" is one this design adds.

| Feature | Asked like | Level | Tool |
| --- | --- | --- | --- |
| A meeting's quality and its components | "이 회의 품질 어땠어?" | read | have `meeting_quality` |
| The team's quality trend, confirmation and completion rates, overdue items | "요즘 우리 팀 어때?" | read | have `team_trend` |
| Gap patterns the team keeps leaving | "우리가 자주 빠뜨리는 게 뭐야?" | read | have `recurring_gaps` |
| Misalignment risk | "결정이 뒤집힐 위험 있어?" | read | have `misalignment_risk`, docstring widened from "only for a report or briefing" to a person's question; #27's gate unchanged |
| Role-pair alignment | "PM이랑 개발 사이 어때?" | read | new `role_alignment`: `get_heatmap`, pairs under three meetings left out |
| Meeting reports and their state | "어제 회의 리포트 보여줘", "게시됐어?" | read | new `meeting_reports`: filtered by date and title, each with draft / waiting / posted, who edited, correction state; new `meeting_report_body` for one report's text (section 4, "Tool contracts") |
| Weekly reports | "지난주 주간 리포트 보여줘" | read | new `weekly_reports`: latest bodies and whether each went out |
| The weekly schedule | "주간 리포트 언제 나가?" | read | new `weekly_report_schedule` |
| What a metric means, how it is computed | "결정 밀도가 뭐야?", "왜 C등급이야?" | read | new `explain_metric` (section 5) |
| Change the weekly schedule | "주간 리포트 금요일 6시로 바꿔줘" | **L1** | new `set_weekly_report_schedule`, recorded under the person who asked; offered in chat since #874 (section 4; "Changes during implementation", 15) |
| Re-draft a meeting's report before it is posted | "최신 수치로 다시 써줘" | L1 | have `draft_meeting_report`, through the template (section 4) |
| Ask again for a report's post | "리포트 올려줘" | **L2** | have `publish_meeting_report` |
| Ask again for a correction's post | "정정 올려줘" | **L2** | have `publish_meeting_report_correction`, only for a correction a person wrote on the dashboard |

The weekly schedule is L1 because any member may change it on the dashboard
without approval (#821). The same person asking in chat gets the same right.

A meeting's draft is made automatically when its analysis finishes. That is
today's template path, and nothing here changes it. Chat re-drafts only
before the post. A posted report is never re-drafted; a person corrects it on
the dashboard, and the correction goes under the post as a reply once
approved (#658).

## 4. Shape and flow

```
report subagent (subagents/report/)
 ├─ template.py   woken by intelligence.completed or meeting_report_changed:
 │                today's graph, moved here unchanged
 └─ chat.py       a chat request: a Gemini tool loop over E's tools
       ├─ reads → answer text
       └─ actions → ProposedAction (never executed here)
             ├─ set_weekly_report_schedule        L1
             ├─ redraft(meeting_id) → template.compose → draft_meeting_report   L1
             └─ publish_meeting_report / _correction                            L2
```

`graph.build` picks the path from the request. A trigger's request is the event
name (`subagents.py`); anything else is chat.

### The chat loop

- **Model:** `GeminiTools` from `main/gemini.py` (`gemini_tools_from_settings`),
  so every request goes through `check_outbound`. No model configured: the agent
  answers that chat is unavailable and links the dashboard. *(Changed: it runs
  the template path instead. See "Changes during implementation", 1.)*
- **Budget:** the same discipline as the ask loop (`main/ask.py`):
  - one-sentence tool declarations
  - compacted tool results
  - at most three rounds
  - a stop before a request passes the size limit (`SIZE_LIMIT`, 3800 of the
    4000 characters `check_outbound` allows). The request is fitted by `_fit`;
    see "Changes during implementation", 5 and 6.

  The loop is E's own rather than `ask.ask`, because `ask` is read-only and
  `main/`'s.
- **Instructions:**
  - Numbers come only from tool results.
  - What a number means comes only from `explain_metric`'s passages. Without
    one, the agent says it does not know.
  - Answer in Korean. Never name one person's share of speech.
- **Actions:** each action is declared to the model as a tool. A call becomes a
  `ProposedAction` in the `SubagentResult`, and the model is told "proposed,
  not done". The main agent then runs L1 at the end of the run and queues L2
  for an approver (`store.run_and_record`, `pending.queue_l2`), as today.
- **Re-draft:**
  - The model calls `redraft(meeting_id)`, never `draft_meeting_report` with a
    body. `redraft` reads `meeting_reports` first. *(Changed: it reads
    `meeting_report_body`. See "Changes during implementation", 2.)*
  - **Posted:** no proposal. The answer points to a correction on the dashboard.
  - **Edited by a member:** no proposal. The answer names the editor and links
    the dashboard, so a person's text is not overwritten from chat.
  - **Otherwise:** `template.compose` builds the body with today's tool order,
    and the result is proposed as `draft_meeting_report` with a new `draft_id`
    **and the `draft_id` it read** (`replaces_draft_id`).
  - **Checked again when it runs.** L1 runs after the graph, so a member can
    edit the draft in between. `save_meeting_report` gains
    `expected_draft_id`. Under the row lock it refuses (`ConflictError`, "the
    draft changed") when the stored `draft_id` is no longer the one read, or
    `edited_by` is set. `draft_meeting_report` passes `replaces_draft_id`
    through and returns "refused: draft changed". The template path passes
    none and keeps today's behaviour (section 8).
- **Scope:**
  - The team and the asker come from `RunScope` (set by `/api/agent/chat` after
    its membership check). `Toolbox.call` refuses another team's id whatever
    the model writes.
- **Who changed the schedule** (`set_weekly_report_schedule`):
  - The asker must never come from the model. Today `ASKER_PARAMETER` is
    filled for reads only (`Toolbox.call`; "actions bind through
    bind_scope, which never does this", `main/registry.py`). An action's
    `user_id` would be whatever the model wrote.
  - **Dependency on `main/` (김민경):** `run_action` fills an action's
    `ASKER_PARAMETER` from `RunScope.user_id`. It overrides any value in the
    proposal and refuses when the scope has no asker. L1 already runs with
    `user_id=requested_by` (`store.run_and_record`).
  - The chat path does not declare `user_id` to the model, so the model has no
    slot to fill.
  - **Until that lands,** the schedule change is not offered in chat. The
    agent shows the current schedule and links the dashboard card. *(Landed
    as #874; the chat path offers it as `set_schedule`. See "Changes during
    implementation", 15.)*
- **Posting from the team view** (and, per "Changes during implementation", 10,
  not only there):
  - `queue_l2` supersedes a subagent's earlier proposals by the **run's**
    `meeting_id` (`main/pending.py`). A run scoped to the team has none, so
    each "리포트 올려줘" from the team view would add one more approval card.
  - **v1:** `publish_meeting_report` and `publish_meeting_report_correction` are
    proposed only in a run scoped to that meeting. In a team-scoped run the
    agent finds the meeting (`meeting_reports`) and answers with a link to its
    screen, where the request is meeting-scoped (#733).
  - **Later, with 김민경:** `queue_l2` supersedes by the proposal's own
    `meeting_id` argument when the run has none. Then posting from the team
    view can be allowed.
  - A duplicate card would not post twice either way: E's post is claimed
    once (`sent_at`). The cost is only noise for approvers.
- **When L1 fails after the answer:**
  - The answer is written before L1 runs. It says what was *requested*
    ("요청했습니다"), never that it is done ("바꿨습니다").
  - The chat reply counts the L1 that worked (`ChatReply.executed`); showing
    one that failed is asked for in #862 (section 7).
- **Failure:**
  - A failed tool: the answer says that part could not be fetched.
  - A model error: one apology line and the dashboard link.
  - `PrivacyViolationError` fails closed (no answer), as the ask loop does.

### Tool contracts: filters, fields, budgets

The ask loop cuts every item body to 80 characters (`BODY_CHARS`, `main/ask.py`).
That would cut a report or a formula in half, so the E loop compacts per tool.
Relative dates ("어제", "지난주") are resolved by the model against a line in
the instructions: today's date in KST (`ACTION_PROGRESS_TODAY_ZONE`).

| Tool | Parameters | Returns per item | Budget |
| --- | --- | --- | --- |
| `meeting_reports` | `since`, `until` (KST dates, optional), `title_contains` (optional), `limit` ≤ 5 | meeting id, title, date, status (draft / waiting / posted), editor, correction state | 5 items, 120 chars each; no body. *(Changed: status is draft / posted, and the correction state is the dashboard's; see "Changes during implementation", 3.)* |
| `meeting_report_body` (new) | `meeting_id` | the stored body | one body, ≤ 1500 chars |
| `weekly_reports` | `on` (a KST date inside the week, optional; default latest) | period, whether it went out, body | one report, ≤ 1200 chars |
| `explain_metric` | `question` | passage title and text | 3 passages, ≤ 400 chars each |
| `team_trend`, `meeting_quality`, `recurring_gaps`, `role_alignment`, `misalignment_risk`, `weekly_report_schedule` | as today | summary and items | summary ≤ 300 chars, items ≤ 120 chars |

- **Identifying a meeting:** the model calls `meeting_reports` with the
  date or title it was given.
  - **One match:** use it.
  - **Several:** the answer lists them (title and date) and asks which one.
  - **None:** say so. It never guesses an id.
- **Over budget:** the request stays under `SIZE_LIMIT` by dropping older
  turns' bodies first and then trimming the largest result, never by cutting
  a formula passage mid-sentence.

### What changes where

| Where | Change | Owner |
| --- | --- | --- |
| `modules/intelligence/.../tools.py` | six reads, one L1 action, `misalignment_risk`'s docstring, `replaces_draft_id` on `draft_meeting_report` | 이승환 |
| `modules/intelligence/.../service.py` | `expected_draft_id` on `save_meeting_report`, checked under the row lock | 이승환 |
| `modules/intelligence/.../glossary/`, `retrieval.py`, `eval` | the metric glossary and its retriever (section 5) | 이승환 |
| `agent/.../subagents/report/` | `template.py` (moved), `chat.py`, the wider description, `redraft` | 이승환 |
| `docs/architecture/agent-layer.md` 3.1, 4 | the Report row and E's tool row | shared, in the same PR |
| `main/` | `run_action` fills an action's asker from the scope; later, `queue_l2` supersedes by the proposal's meeting in a team-scoped run; whether a chat reply shows L1's result | 김민경, asked in an issue (section 7) |

## 5. The metric glossary and its retrieval

### The corpus

About 20 to 40 passages, one per `##` section of Markdown files under
`autune_intelligence/glossary/`:

- the quality score's four components, weights, grade cutoffs, and what "not
  measured" means
- each gap pattern type
- the heatmap and its three-meeting floor
- what the misalignment prediction predicts, its known accuracy limits, and
  #27's display gate
- completion vs confirmation rate, overdue and carried-over items, their spans
  (four weeks / every kept meeting) and the three-meeting floor
- the meeting report's draft, approval, correction and the weekly schedule

Rules for the corpus:

- **Numbers come from code.** (Placeholders are filled by `glossary.fill()`; see "Changes during implementation", 8.) A passage writes `{weight.decision_density}`,
  not "30%". The loader fills it from `WEIGHTS`, `GRADE_CUTOFFS`,
  `ACTION_COMPLETION_WINDOW`, `ACTION_PROGRESS_MIN_MEETINGS`, and similar
  constants, so the glossary cannot drift from the code.
- **Korean** (see "Language" below).
- **Static.** No meeting content and no person. Nothing to delete, no
  retention window.

### Language

The passages are stored in Korean. `CLAUDE.md` section 0 puts everything written
into the repository in English. The repository already keeps Korean user-facing
text beside English code, though:

- tool summaries in `tools.py` ("이 회의의 품질 점수가 아직 없습니다.")
- the report footer (`MEETING_REPORT_FOOTER`)
- every string on the web screens

The glossary is the same kind of text. It is response content a person reads,
quoted into an answer. This design names that exception, so the PR asks the
team for it explicitly:

- **Korean:** the passage text under `glossary/*.md`, and the evaluation
  questions (they are what a person types).
- **English:** file names, headings' anchor keys, the loader, the retriever,
  comments, docstrings, the evaluation's code and output labels.

Korean passages also keep BM25 useful: a Korean question shares no terms with
an English passage, and a model translating an English definition at answer
time could change its meaning.

### Retrieval

```
question ─┬─ BM25 over kiwipiepy morphemes ─┐
          └─ dense: cosine on embeddings   ─┴─ RRF (k = 60) ─▶ top 3 passages
```

- **Dense model:** `paraphrase-multilingual-MiniLM-L12-v2`, the backbone E
  already loads for the gap classifier (`gap_classifier_backbone`). Nothing new
  to download, and the question never leaves the process. D's KURE-v1 is
  stronger on Korean but about 2 GB; it is a comparison row in the evaluation
  only.
- **BM25:** `kiwipiepy` and `rank-bm25`, as D uses. They are added to
  `modules/intelligence/pyproject.toml` (invariant 8).
- **Index:** in memory, built on first use. The corpus is small and fixed, so
  there is no table and no pgvector.
- **Choice of implementation:** `AUTUNE_INTELLIGENCE_RETRIEVER_IMPL`, as the
  gap classifier's impl setting works.
  - `bm25` needs no model. It is the default and what CI runs.
  - `hybrid` needs the `local-models` extra.
- **Agent rule:** no LangChain retriever reads the database (`agent-layer.md`,
  "no LangChain tools or retrievers that reach outside"). Retrieval is E's
  tool `explain_metric(question)`, which returns the passages as items
  (title, text).

### Evaluation

`python -m autune_intelligence.eval retrieval` scores a fixed set of about 30
questions, each paired with its expected passage. The set mixes definitions,
paraphrases and "why" questions. The command prints recall@1, recall@3 and MRR
for BM25 only, dense only and hybrid. The results decide the default
implementation and are recorded in `docs/modules/intelligence.md`.
*(Changed: the command is `python -m autune_intelligence.retrieval_eval`, and
the measurement kept BM25; see "Changes during implementation", 4 and 7.)*

## 6. Testing

- **E tools** (`modules/intelligence/tests`):
  - each new read: members only, team scope, floors kept
  - `set_weekly_report_schedule`: recorded under the asker; refused for a
    non-member
  - the glossary loader: every `{placeholder}` filled; no passage cites a number
    that the constants do not hold
  - `explain_metric` with `bm25`: the right passage for a definition question
- **The chat path** (`agent/tests`), with a scripted fake `ToolModel`:
  - a read question is answered from tool results
  - "금요일 6시로 바꿔줘" yields one L1 proposal with the asker
  - "리포트 올려줘" yields one L2 proposal
  - a member-edited draft is not re-drafted, and the answer names the editor
  - a posted report is not re-drafted
  - another team's id is refused
  - the loop stops at the size limit
  - a model error ends in the apology line
- **Concurrency, asker and team view** (review, 2026-10-05):
  - re-draft vs edit: a draft read, then edited by a member, then the L1
    re-draft runs. It is refused ("draft changed"), and the member's text and
    name stay. Also a draft re-drafted by the automatic path in between.
  - asker forgery: a proposal carrying another person's `user_id` is
    recorded under the real asker. With no asker in the scope it is refused.
    This test lives with `main/`'s change. E's side tests that the chat path
    declares no `user_id`.
  - repeated posts from the team view: two "리포트 올려줘" in team-scoped
    runs leave no L2 proposal and two answers linking the meeting.
  - L1 failing after the answer: the reply says "요청했습니다", and the run
    records the failure.
- **The template path:** today's tests pass unchanged after the move.
- **Retrieval:** the evaluation above, run by hand and recorded; CI runs only
  the `bm25` unit tests.

## 7. Delivery

0. **#862 to 김민경** (opened 2026-10-06):
   - fill an action's asker from the scope in `run_action`
   - supersede by the proposal's meeting in a team-scoped run
   - show a failed L1 in the chat reply (S34 shows only `executed`)

   Steps 1 to 3 do not wait for it. Only the chat schedule change does. The
   workarounds live in one place in `chat.py`: the list of actions the chat
   path declares (without `set_weekly_report_schedule`) and one
   meeting-scope check before a post is proposed. Lifting them once #862
   lands is a few lines and their tests. *(All three landed: request 1 as #874,
   and the schedule change is lifted (#911); request 2 as #896; request 3 as
   #897. #879's two follow-ups landed as #923 and #924. See "Changes during
   implementation", 10 and 12.)*
1. **E's tools and the glossary** (module E, plus `agent-layer.md` section 4),
   with `expected_draft_id`. #821, which this needed, is merged. *(Built as
   the plan's Tasks 1 to 7; see "Changes during implementation", 9.)*
2. **The chat path** (`subagents/report/`, plus `agent-layer.md` section 3.1).
   It uses the tools from step 1. *(Plan Tasks 8 and 9.)*
3. **The retrieval evaluation**, with its numbers in the docs.

## 8. Later, and related gaps

### Report search (a second RAG)

"결제 API 얘기한 리포트 찾아줘" over meeting report bodies, corrections and
weekly reports. Not in this design, because it carries meeting content:

- chunks in an `intel_` table with pgvector
- each chunk with a foreign key to `meetings`, `ON DELETE CASCADE`
- re-embedded when a draft is edited, a correction is written, or speech is
  forgotten (#614's path)
- team scope and the retention window applied inside the search

### A rejected approval is invisible on the dashboard

Approvals live in `agent_pending_actions` (`main/`); E cannot read them. After
a rejection, the card goes on saying "승인 대기" for an edited draft
(`MeetingReportsCard.tsx`). Two remedies, both with 김민경:

1. The agent layer tells E when an approval is decided, and the card shows
   "거절됨 · 사유". This is preferred: the reason says what to change.
2. The E agent answers "is it waiting?" in chat from the approval table, and
   the card stays as it is.

### A redraft on the automatic path overwrites a member's edit

`draft_meeting_report` replaces any unposted draft, a person's edit included.
Chat is guarded here (section 4). Whether a late `intelligence.completed`
should also keep a member's edit is a separate decision for the owner.

## Changes during implementation (2026-10-06)

Where the build departed from the text above, or fixed something it left open.
The sections above are left as designed; each affected one points here.

1. **No model configured (section 4, "The chat loop").** The agent runs
   today's template path for the request instead of saying chat is
   unavailable. "리포트 써줘" keeps working where Gemini is off.
2. **Re-draft (section 4).** `redraft` reads `meeting_report_body`, not
   `meeting_reports`. The same read without a meeting is the meeting-scope
   probe (`NO_MEETING`).
3. **`meeting_reports` (section 4, "Tool contracts").** It gives `draft` /
   `posted`, not "waiting": approvals live in `main/`'s table, which E cannot
   read. Its correction state is the dashboard's: `pending` / `sending` /
   `sent` / `failed`.
4. **Evaluation (section 5).** The command is
   `python -m autune_intelligence.retrieval_eval`.
5. **Chat: one action each per run (section 4, an addition).** Within one
   run, `redraft` and `request_post` each act once. A second call answers
   "이미 요청했습니다." The run keeps at most one post proposal, and
   `redraft`'s post replaces an earlier one. Two approval cards for one post
   are noise, and one of them would point at a replaced draft.
6. **Chat: fitting the request (section 4, "Budget").** A deviation from
   section 4's "never by cutting a formula passage mid-sentence". `_fit` in
   `chat.py` drops bodies from older turns first, then halves the longest body
   in the latest turn, mid-sentence, glossary passages included. It cuts only
   the loop model's view of the turns; the reply is built from the untrimmed
   results. The loop ends only if the request still does not fit `SIZE_LIMIT`.
7. **Evaluation results (section 5).** BM25, dense (the gap classifier's
   MiniLM) and hybrid were measured on 2026-10-06. Hybrid ties BM25 at
   recall@3, the number that matters for a three-passage answer, and gains
   only in ordering, so `retriever_impl` stays `bm25`: hybrid would also need
   the `local-models` extra and the model wherever the API and worker run.
   KURE was not run. The numbers are in `docs/modules/intelligence.md`, not
   copied here.
8. **Glossary placeholders (section 5).** They are written `{area.name}` and
   filled by `glossary.fill()`, not `str.format`, because the dotted names
   break `str.format`.
9. **Delivery (section 7).** Steps 1 to 3 map to the plan's Tasks 1 to 7
   (module E: the tools, the glossary, the retrieval evaluation) and Tasks 8
   and 9 (the chat path).
10. **Known v1 limit: a stale or doubled card (section 4, "Posting from the
    team view").** The premise that only the team view is affected is too
    narrow. `queue_l2` (#651) lets a meeting-scoped chat supersede only
    earlier *chat* rows, never the pipeline's pending post card. So even in
    the meeting's own view:
    - after `redraft`, the pipeline's card points at the replaced draft, and
      approving it is refused as "draft not current";
    - "리포트 올려줘" while the pipeline's card waits makes a second live card
      for the same draft.

    Also, `redraft` of meeting X from the team view, or from another
    meeting's view, proposes X's new draft but no post, for the reason in
    section 4. #862's item 2 covers only team-scoped runs. This needs a
    further `main/` change: a chat redraft or post supersedes the pipeline's
    post row for the same meeting.

    *Resolved in `main/` (2026-10-06):*
    - **#924:** a chat proposal of the same action now supersedes the
      pipeline's pending card for that meeting, so neither card above stays
      live.
    - **#896:** a team-scoped run's L2 is keyed on the meeting its arguments
      name. E still proposes a post only from a meeting-scoped run, though:
      `request_post` from the team view points to the meeting screen.
      `publish_meeting_report`'s arguments carry the `draft_id` only, so
      lifting that is an E change. The post proposal would have to name its
      `meeting_id`.
11. **Links (section 4).** The answers name the meeting screen and the
    dashboard card in words. They do not link them, unlike section 4's
    "links".
12. **Router dependency (section 1).** `main/gemini.py`'s `ROUTE_INSTRUCTIONS`
    sends a request that "only asks to look something up" to the ask loop
    (null route). That loop holds only three of E's reads. So lookups such as
    "결정 밀도가 뭐야?" or "주간 리포트 언제 나가?" may never reach the E agent
    until the router's rule changes. That rule is `main/`, 김민경's. Section
    1's goal depends on it.

    *Resolved (2026-10-06/07):*
    - **#923:** a subagent can declare `answers_lookups=True`, and a lookup
      that fits its description then reaches it. The Report subagent sets it.
    - **Checked on a copy of the dev DB with the real model** (2026-10-07):
      "결정 밀도가 뭐야?", "완료율은 어떻게 계산돼?", "주간 리포트 언제 나가?",
      "요즘 우리 팀 회의 품질 어때?", "이 회의 리포트 보여줘", "리포트 올려줘" and
      "다시 써줘" reached Report on every run and called the right reads.
    - **"PM이랑 개발 사이 입장 차이 어때?" went to Research on one run in two.**
      Research's description names "disputes". Report's description now says
      that role alignment is how far roles such as PM and engineering agree or
      differ in their stances. After that, the question and a reworded one
      reached Report on every run (five of five). A Research question ("아무도
      확인 못 한 쟁점 조사해줘") still went to Research.
    - **Weekly-report lookups went to the ask loop** (2026-10-07, synthetic
      team only; see the next point). "지난주 주간 리포트 보여줘" and "주간 리포트에
      뭐라고 나왔어?" went to the ask loop on four runs of five, and
      `audio.recent_meetings` answered with a list of meetings. Report's
      description now names the weekly report E posts to the team channel, and
      what a person asks of it. After that, five of five reached
      `weekly_reports`. "지난주에 무슨 회의 했어?" and "이번 주 내 할 일 뭐 남았어?"
      still went to the ask loop.
    - **Real-model checks use a synthetic team only** until #935 is decided.
      The checks above, from 2026-10-06/07, read a copy of the local dev DB,
      which holds the team's recordings; the facts are on #935. The current way
      is an empty scratch database at main's heads, seeded with one mock team.
13. **`send_empty` (section 4).** `set_weekly_report_schedule`'s `send_empty`
    is optional; `None` keeps the team's current value, so a schedule change
    no longer turns it off.
14. **Once per run, retried after a failure (item 5).** An action counts as
    done only on a terminal outcome: a proposal, `POSTED`, the editor refusal,
    the "회의 화면에서" redirect, or "아직 이 회의의 리포트가 없습니다". A
    failed read (`NO_MEETING` for `redraft`, meeting not found, or any read
    that raised) or a failed compose leaves the retry open. For
    `request_post`, `NO_MEETING` is the team-view redirect, which is terminal.
15. **The schedule change in chat (section 4, after #874).** `run_action` now
    pins an action's `user_id` to the run's asker, so the chat path declares
    `set_schedule(weekday, hour, send_empty?)` and proposes
    `set_weekly_report_schedule` at L1. The declaration has no `user_id` or
    `team_id`. A value out of range goes back to the model to correct and is
    not reported as a missing part; if no call ever fits, the reply asks for a
    weekday and an hour 0-23. The reply spells out 오전/오후, because "6시"
    may mean either and the model picks one.
