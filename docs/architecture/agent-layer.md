# The agent layer

> **Status: Decided.** The direction was decided on #260 (closed 2026-09-29)
> and the layer's location is ADR 0010, `Accepted`. Nothing described here is
> built yet; the skeleton is #432. The two questions that blocked the first line
> of code are answered: the layer lives in a top-level `agent/` (13.1) and a
> periodic trigger is a `@periodic` task (13.2, #374). **Who builds what is in
> section 3.1, and the dates are in section 14** — start there if you are
> picking up a subagent.

Modules A–E are exposed as **tools**. An agent layer above them decides which
tools to call, when to wake up, and what it is allowed to do with the answer.

This document covers only the layer. Module boundaries
(`module-boundaries.md`), contracts (`contracts.md`) and the event pipeline
(`async-pipeline.md`) are unchanged by it, and that is the point.
`packages/contracts` in particular gains **no field and no event** — section 8
rule 4 says how the agent learns what it needs without one, because "contracts
unchanged" is a claim that needs a mechanism behind it.

---

## 1. Why — the loop that is missing

A–E are **transformers**. One recording goes in, one set of artefacts comes
out, and the run is over.

```
recording ──> [A] ──> [B][C][D] ──> [E] ──> minutes · actions · gaps · links · dashboard ──> done
```

Nothing in that line ever runs a second time on its own. Three things are
missing, and they are not features:

| Missing | Today | Consequence |
| --- | --- | --- |
| A reason to wake up | One trigger: a person uploads a recording | Nobody calls, nothing moves |
| Something to look at | Per-meeting artefacts only; no state that spans meetings | Even awake, there is no way to ask "what is stuck?" |
| Something to do | Every output is "render this" or "send this once" | No path from a judgement to a next move |

The gap is a **loop**, not a model. A pipeline flows once; an agent keeps
going. So what gets added is three pieces of plumbing — state, triggers,
permitted actions — and no new machine learning.

## 2. What changes, and what does not

**Does not change:**

- Module internals. A–E keep their services, tasks, tables and contracts.
- The fixed pipeline. `autune.transcript.ready` → B, C, D → `…completed` → E
  keeps running exactly as it does now. **The agent layer is an optional path
  on top; if it is switched off or broken, the product still works.** That is
  the primary risk control in section 12.
- Invariant 2. Modules still may not import one another.

**Changes:**

- Each module gains one new file, `modules/<name>/src/autune_<name>/tools.py`,
  listing three to five callable tools. Nothing else in the module is touched.
- A new layer in `agent/` (ADR 0010) holds the main agent, five feature
  subagents, the tool registry, the work-item store and the triggers. The main
  agent has one owner and each subagent has one owner (section 3.1).
- New tables with an `agent_` prefix (section 5, and `data-model.md`).

## 3. Shape

```
  triggers                  ┌─────────────────────────────────────┐
  ─────────                 │  Main agent (orchestrator)          │
  scheduler ───────────────>│  route → delegate → combine → log   │
  meeting completed ───────>│  chat, work items, approval gate    │
  chat message ────────────>└──────────────────┬──────────────────┘
                                               │ delegates one task
         ┌──────────────┬──────────────┬───────┴──────┬──────────────┐
         ▼              ▼              ▼              ▼              ▼
     Research        Briefing      Follow-up      Workload        Report
     (김민경)         (문민재)       (박재경)        (강민구)        (이승환)
         │              │              │              │              │
         └──────────────┴──────┬───────┴──────────────┴──────────────┘
                               │ every subagent calls tools; none calls another
                               ▼
                         tool registry
                               │
        ┌──────┬───────┬───────┼───────┬────────┐
        ▼      ▼       ▼       ▼       ▼        ▼
       [A]    [B]     [C]     [D]     [E]   integrations
        modules, unchanged           (Slack, Notion, Jira, Calendar;
                                      outbound boundary, section 8 rule 1)
                               │
                               ▼
              agent_work_items · agent_runs · agent_approvers
```

**Modules are tools; features are subagents.** A module answers questions
about what it already computed — B's action items, C's topic graph, D's links
— through its `tools.py`, and gains no loop. A subagent is a feature: it reads
from several modules and makes a judgement between the reads, and that is the
test an earlier draft of this document set for when a subagent earns its cost.
"Detect gaps for this meeting" is one call and stays a tool. "Decide whether
this team needs another meeting" reads open items from B, undismissed gaps from
C and the last decision thread from D, then weighs them — that is a subagent.

An earlier draft had one subagent per *module* and rejected it for the right
reason: a loop in front of a single tool call is cost without benefit, and the
return contract (section 4) already gives the orchestrator context isolation.
That argument still holds and still rules out a per-module subagent. What
changed is that the product now has five features that each span modules, and
each one of them passes the test on its own.

### 3.1 Who builds what

One owner per box. The main agent's owner builds the loop every subagent runs
inside; each subagent's owner builds that subagent, its prompts and its tests,
and keeps their module's `tools.py`.

| Part | Owner | What it does | Wakes on | Reads (tools) | Leaves the building as |
| --- | --- | --- | --- | --- | --- |
| **Main agent** | 김민경 | Chat entry point; routes a request or a trigger to one subagent, or answers from tools directly; combines the answer; owns the work-item store, the trigger scheduler, the approval gate and `agent_runs` | every trigger, every chat message | any | the chat answer; L2 plans to the approval screen |
| **Research** | 김민경 | When a meeting raises an idea or argues over a fact nobody could confirm, gathers what is known into a short document and proposes sending it to the people involved | `autune.intelligence.completed`; a chat request | A (the team's meetings), B (open questions); D once it ships tools.py. Uploaded material has no store yet | a document shown to the team in the app after an approver with scope `research` approves it — L2; a Slack DM to participants follows #478 |
| **Briefing** | 문민재 | Ten minutes before a meeting, sends the previous meeting's summary and the issues this one should settle; lists the team's open Jira issues (B's `TeamAgenda`, #436) | time, from Google Calendar (`list_events`) | D (links, decision threads), B (open items), C (undismissed gaps and their questions), the team's open Jira issues as B reported them (`brief_agenda`) | D's pre-meeting brief — D's own surface, rule 2 |
| **Follow-up** | 박재경 | Watches the gaps nobody closed; when a follow-up meeting looks needed, proposes one — to the team lead only | `autune.intelligence.completed`; a chat request | C (undismissed gaps; the template items left open in this meeting and the team's previous one), B (unresolved questions; whether a Follow-up item is still open), A (the team's latest meeting, on a chat run about none). No participation, no `silent_share`, no calendar | a proposal on the lead's approval screen; after approval, an unconfirmed "후속 회의 잡기" item on the board (B's `add_followup_item`) — L2. It reaches a calendar only through B's sync, once a person confirms it with an assignee and a due date (#441) |
| **Workload** | 강민구 | Notices that one person is overloaded while another has finished, and proposes a redistribution — to the manager only; owns the Gmail, Google Calendar and Jira integrations | state, `@periodic` | B (items per owner and their state), Calendar (`free_busy`), Jira only after #82 | a proposal on the manager's approval screen; any reassignment only after approval — L2 |
| **Report** | 이승환 | After a meeting, composes its structured minutes from a template (no LLM) and proposes that E store and post them | `autune.intelligence.completed`; a chat request | B (confirmed action items, review-state counts); C's open gaps (`gap.open_gaps`, HIGH and MEDIUM listed, as S20 shows them; LOW only in C's count); D (linked meetings, by title and date only) once its `tools.py` ships — until then that section is absent. Not E's scores: the report carries no quality grade | a draft stored by E at L1 (`draft_meeting_report`); the channel post through E's report delivery at L2 (`publish_meeting_report`) — E's own surface, rule 2 |

Three things in that table are decisions, not descriptions:

- **A proposal to a lead is the approval request itself.** Follow-up and
  Workload do not DM the lead and then ask someone to approve the DM. The
  proposal lands on the lead's approval screen (section 8, plan mode), and what
  it proposes — a follow-up item on the board, a reassignment, a message to
  the people affected — happens only when the lead approves it item by item.
- **Workload and Follow-up read counts of work, never speech.** How many open
  items a person owns and how late they are is work state, which a manager
  already sees on a task board. How much a person spoke, or whether they were
  silent on a topic, is not: privacy.md section 3 keeps a speaking ratio with
  its speaker and forbids per-person speaking patterns to anyone else, managers
  included. **Per role is no better**: in a team with one person per role, a
  role is a person, and the reader is the lead. So Follow-up reads C's
  topic-level results only — the gaps nobody dismissed — and never
  participation per role or per person. It does not read a topic's
  `silent_share` either: the figure is per topic, but in a two- or three-person
  meeting a share of one-half says a lot about one person, and Follow-up's rule
  does not need it (its spec, section 6). No speaking-ratio tool is registered
  at all (section 4, `PERSONAL_ONLY_TOOLS`).
- **Research reads what we hold, not the open web.** The team's past meetings through A's tools and open questions through B; past meetings through D once D ships its `tools.py`. Uploaded material would belong here too, but there is no store for it yet. Open-web search is still out
  of scope (section 13.3); a subagent owner who wants it raises it there rather
  than adding a search tool.

**Integration work runs ahead of the subagents that need it.**

- **Google Calendar is in place.** #438 (#435) gave `CalendarClient` the reads
  these subagents were to need: `list_events` for Briefing's upcoming
  meetings, `free_busy` and `create_event`. As built, Follow-up reads no
  calendar: a team account sees one Workspace only, and a person's own grant
  serves only their own work (#435). An approved follow-up reaches a calendar
  through B's sync instead, as an all-day event on its assignee's own calendar
  once a person confirms it with a due date (#441). The client reads **busy
  windows only** — never titles, attendees or places of other people's events —
  and returns `None` for a calendar it could not read, which a subagent must
  not treat as free.
- **Jira is back.** It was evaluated and dropped (#82) and brought back over
  one-click OAuth 3LO (#457, #458; `integrations.md`). 강민구 owns Jira (agreed
  with 문민재 on #260): `JiraClient` on 3LO and B's action-to-issue sync are in,
  and Briefing reads the team's open issues as B reports them (`TeamAgenda`,
  #436, through D's `brief_agenda`). Workload reads no Jira yet and is
  specified to work without it.
- **Gmail is new** (section 13.6).

### 3.2 Where the code goes

```
agent/
├── pyproject.toml                 autune-agent; LangGraph and the LLM SDK live here
└── src/autune_agent/
    ├── main/                      main agent — 김민경
    │   ├── graph.py               the supervisor graph, section 3.3
    │   ├── registry.py            tools, collected by iterating the module list
    │   ├── triggers.py            @periodic and event subscriptions
    │   ├── approval.py            plan mode, suspend and resume
    │   └── store.py               agent_work_items, agent_runs, agent_approvers
    └── subagents/
        ├── research/              김민경
        ├── briefing/              문민재
        ├── followup/              박재경
        ├── workload/              강민구
        └── report/                이승환
```

CODEOWNERS follows the tree: `/agent/src/autune_agent/main/` to the main
agent's owner and each `subagents/<name>/` to its owner, so a subagent change
needs its owner's approval and nobody else's. A change under `main/` touches
every subagent and needs the main agent's owner.

**Subagents never import each other**, for the same reason modules do not
(invariant 2). If Follow-up needs what Workload knows, it asks the main agent,
which routes. A fifth import-linter contract, alongside ADR 0010's fourth,
makes the `subagents.*` packages independent, and a sixth forbids a subagent
importing a module at all: it reads through its `Toolbox`, which holds its
allow-list and the run's budget, or not at all.

Each subagent directory exports one thing, collected the way tools are — by
iterating the subagent list, never by appending to a registry:

```python
# agent/src/autune_agent/subagents/followup/__init__.py
SUBAGENT = Subagent(
    name="followup",
    description="""Use when deciding whether a team needs another meeting ...""",
    tools=[...],  # names from C's, B's and D's own tools.py
    build=build_graph,  # returns a compiled LangGraph subgraph
    triggers=[Periodic(hours=6)],
)
```

`description` is what the main agent routes on, so it says **when to use it**
first, like a tool's docstring (section 4, rule 2). `tools` is an allow-list:
a subagent sees only the tools it names, which is how section 3.1's rule that
Workload never sees a speaking ratio is enforced rather than hoped for.

A subagent hands back the same `ToolResult` a tool does (section 4) — a
summary, at most five items, evidence ids — plus, when it wants something done
at L2, a list of `ProposedAction` for the main agent to put through plan mode.
**A subagent never calls a write tool itself.** The main agent owns the gate.

### 3.3 LangGraph, and what it is not allowed to do

The main agent is a LangGraph supervisor graph; each subagent is a compiled
subgraph the supervisor delegates to. An earlier draft said "no framework — a
hand-written loop of about 200 lines", and with one orchestrator and one
subagent that was right. With five subagents built by five people it is not:
a shared graph shape is what lets each owner build a subagent without
re-inventing routing and tool calling. An earlier draft also made LangGraph's
`interrupt` plan mode's pause point; as built, plan mode is an approval queue
outside the graph (section 8), so no graph waits for a person.

**Why not LangChain's `create_agent`.** It was suggested as the way to build
the agent, and the layer does not use it. `create_agent` is a loop in which the
model picks and calls tools itself, and three rules of this layer sit badly
with that: every model call must go through `check_outbound` and
`assert_masked` (section 8 rule 1), which a LangChain chat model would bypass;
a subagent never runs a write, it returns `ProposedAction`s (rule 4 in
`agent/CLAUDE.md`); and tools come from the registry with an allow-list and a
call budget per run (section 4). So each subagent is a fixed `StateGraph` and
the model only routes and writes. None of the three is impossible to adapt —
a guarded chat-model wrapper and a registry-to-tool adapter would do — so if
the free-form chat path ever needs the model to choose tools, `create_agent` is
the first thing to try there, with the event-driven subagents left as they are.

Two uses of LangGraph are **not** allowed, because each would bypass a rule
this repository already enforces:

- **No LangGraph checkpointer tables.** Its Postgres checkpointer creates
  unprefixed tables that hold the full message state — copies of tool results —
  with no deletion path by `meeting_id`. That fails invariant 3 and
  privacy.md section 7 at once. The run persists to `agent_runs.messages`
  (section 5), and resume rebuilds the graph state from that row; an in-memory
  checkpointer inside a single run is fine.
- **No LangChain tools or retrievers that reach outside.** Every tool the graph
  calls comes from the registry in section 4. A prebuilt web-search tool, a
  document loader that fetches a URL or a LangChain retriever over our database
  would each be an outbound path or a read that skips a module's own visibility
  rules.

The LLM behind the graph is Gemini, through the same `check_outbound` /
`assert_masked` path B's `classifier_impl=llm` already uses (#393; section 8
rule 1). The model name is configuration, not code.

## 4. Tools — how a module becomes callable

A module owner adds one file and changes nothing else. **It imports nothing
from `autune_agent`** — ADR 0010's layers contract forbids a module importing
the layer — so a tool is a plain function returning a plain dict, and the
registry validates the dict into `ToolResult` when it calls it. B's `tools.py`
(#399) is the first one and the pattern:

```python
# modules/gap/src/autune_gap/tools.py
def unresolved_topics(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this when asked which topics a meeting left open, or before
    proposing a follow-up meeting. Do not use it for what was decided --
    that is D's decision thread.

    Returns the open topics, most central first, at most five.
    """
    ...
    return {
        "ok": True,
        "reason": None,
        "summary": ...,
        "items": [...],
        "evidence": ["utt_…"],
        "confidence": 1.0,
        "truncated": False,
    }


TOOLS = [unresolved_topics, ...]
PERSONAL_ONLY_TOOLS = []  # reads that return one person's own data
```

Collected the way `apps/api` collects routers — by iterating the module list,
never by appending to a registry (invariant 6). `autune_agent.main.registry`
names each tool `<module>.<function>`:

```python
for name in MODULES:
    module = import_module(f"autune_{name}.tools")  # skipped if absent
    for fn in module.TOOLS:
        if fn in module.PERSONAL_ONLY_TOOLS:
            continue  # never registered
        REGISTRY[f"{name}.{fn.__name__}"] = Tool(fn)
```

**`PERSONAL_ONLY_TOOLS` keeps a speaking ratio out of the layer entirely.** A
read that returns one person's own data goes to that person and nobody else
(invariant 11), and the agent always answers on someone else's behalf, so the
registry never loads such a tool — it is not a matter of each subagent leaving
it off its allow-list. An undeclared tool whose name says `speaking_ratio`
fails collection, and `Subagent` refuses the name too, as a net under the
declaration rather than instead of it (#432 review).

### The return contract — what every tool hands back

```python
class ToolResult(BaseModel):
    ok: bool
    reason: str | None = None  # when ok is False: why, in one line
    summary: str  # three sentences at most; the orchestrator reads this
    items: list[Finding]  # at most five, ranked by importance
    evidence: list[str]  # utterance ids only, never text
    confidence: float
    truncated: bool  # True when the cap cut something off
```

This is the whole of context isolation (section 3). Three things are enforced
by the decorator, not requested:

- **`items` is capped at five.** A tool that found forty-seven ranks them and
  keeps five; the rest go to that module's own tables, where a screen can show
  them. Not in the context and not shown are different things.
- **`evidence` is ids.** The orchestrator fetches text when it needs it, which
  is rarely. Ids are also what keeps a transcript out of a prompt by accident.
- **`truncated` is honest.** The orchestrator may decide to call again with a
  narrower question; it must never decide the meeting had five gaps.

`Finding` carries a title, a body and a score. Its body is masked text like
everything read back from the database (privacy.md section 2); the contract
does not make it more or less so.

### Rules for a tool

1. **Synchronous, with type hints.** Arguments are `packages/contracts` models
   or ids; the return is `ToolResult`. *Not* `async` — this repository is
   synchronous SQLAlchemy and synchronous routes throughout, and an `async def`
   wrapping a blocking call is a lie that costs a thread.
2. **The docstring is the prompt.** Say **when to use it**, and when not to,
   before saying what it does. Half of an agent's accuracy is decided here.
3. **One high-level tool and two or three primitives.** Only the high-level
   tool and the agent cannot compose; only primitives and it gets lost. Three
   to five per module.
4. **Safe to call twice**, with one recorded exception:
   `autune.audio.process_recording` is not, and cannot be. It deletes the
   recording in a `finally` (invariant 11), so a second call has nothing left
   to decode. Module A exposes reads and the transcript, not a re-transcribe.
5. **Thirty-second budget.** Past it, return what there is with
   `truncated=True` rather than blocking the loop.
6. **Never raise for an expected failure.** `ok=False` with a reason, so the
   agent can read it and take another route. Reserve exceptions for bugs.

### What each module's `tools.py` starts from

**Each module's `tools.py` is its owner's file** (ADR 0010), so what follows is
what each module can already answer, not a list of names anyone else gets to
fix. An earlier draft of this document asked for tools that do not exist; the
owners of B, C and D corrected it on #261, and their corrections are what this
says.

| Module | What it can already answer | Note |
| --- | --- | --- |
| B | its action items; the stored classifications; an item's review state | B's read API, nothing new. `list_action_items` exists today |
| C | a meeting's gaps with `risk_score` and `suggested_question`; the topic graph; a topic's `silent_share` | all four of C's steps produce values; what is left is measuring precision on real meetings (#22). Tools are C's owner's, in topic-level form |
| D | this meeting's links; a decision thread; the team's decisions; the earlier meeting an upcoming meeting follows; the Jira issues it should take up | `links_for_meeting`, `decision_thread`, `list_decisions` over #185's read routes, and `brief_recap`, `brief_agenda` over the pre-meeting brief's own rows (Briefing's reads), named by D's owner |
| E | a meeting's quality score; the team's trend; its recurring gap patterns; the misalignment risk (withheld before #27's history gate); a meeting report's stored draft, by `draft_id`, for the approval card | E's aggregate reads. No speaking-ratio tool (invariant 11). Two actions for the Report subagent: `draft_meeting_report` (L1) and `publish_meeting_report` (L2) |

- **C — the charter reaching gap detection is a proposal, to be agreed with
  C.** An earlier draft said `detect_gaps` would take a `checklist: list[str]`
  and called it "the one signature change". It is not one: `detect.compare`
  judges a template item by its `keywords`, `weight`, `question` and
  `category`, without a model or a network, so a line of prose does not run
  through it. Per-meeting template choice already exists (`set_template`,
  `gap_meeting_templates`, S20 in #303). How a team's charter becomes a template
  is C's design, tied to #22, in an issue C's owner opens.
- **D — search is not a tool.** An earlier draft asked for `search_exact`
  (BM25) and `search_semantic` (embeddings) as two tools, on the reasoning that
  the agent should choose between exact match and meaning. D does not work that
  way. There is one `HybridRetriever`: KURE-v1 dense plus BM25 over
  `kiwipiepy`, fused with RRF and then reranked and NLI-checked, so the BM25
  half already catches a name or a Jira key and splitting it would take a
  choice the retriever has already made better. D has no search route at all —
  #185's read API is `GET /links/{meeting_id}`,
  `POST /links/{link_id}/confirm`, `GET /decisions/{thread_id}` and
  `GET /decisions`. And `visible_meeting_clauses` — same team, inside the
  retention window, earlier than this meeting — is applied *inside* the
  retriever, so exposing BM25 and dense separately would mean re-enforcing
  visibility in two more places. D's tools are reads over the links D's
  pipeline already computed. Free-text search over D's corpus is D's design
  decision and belongs in its own issue, not here.
- **B — `classify_utterances` is a read, not a call into the pipeline.**
  Classifying a 45-minute meeting is about two minutes of CPU (#112), well past
  the thirty-second budget below, and `service.classify_utterances` is an
  internal pipeline step that takes the consent filter as a required argument.
  The tool reads the rows the pipeline already wrote to `ext_classifications`.
  `verify_agreement` was in an earlier draft of this document and does not
  exist anywhere in the repository; it is dropped rather than commissioned.

## 5. State — the two tables that make it proactive

The repository has action-item cards. It has no table holding **what is true
about a piece of work right now**, across meetings. That single absence is
what makes the product reactive.

```sql
CREATE TABLE agent_work_items (
  id                TEXT PRIMARY KEY,   -- wi_…
  team_id           TEXT,       -- team_…; every query is per team
  kind              TEXT,       -- action | gap | open_question | decision | risk
  title             TEXT,
  body              TEXT,       -- masked, like everything derived from a transcript
  origin_meeting    TEXT,       -- mtg_…, NULL when it was not born in a meeting
  origin_module     TEXT,       -- b | c | d | e, or NULL; which module's read owns it
  source_id         TEXT,       -- the owning module's own id: act_…, dec_…, NULL otherwise
  origin_utterances JSONB,      -- ["utt_…", …]; a decision spans several
  owner_id          TEXT,       -- user_…
  due_date          DATE,       -- the agent's own view; B owns ext_action_items.due_date
  status            TEXT,       -- open | in_progress | blocked | resolved | dropped
  confirmed         BOOLEAN,    -- as reported by the owning module's read; section 8 rule 3
  confidence        FLOAT,      -- the owning module's value, never ours; section 10
  external_ref      JSONB,      -- read back from the owning module; the agent writes no page
  last_signal_at    TIMESTAMPTZ,
  escalation_lv     INT,        -- 0 watch · 1 DM · 2 raise on agenda · 3 report to lead
  next_check_at     TIMESTAMPTZ,-- when the agent wakes itself for this item
  created_at        TIMESTAMPTZ,
  updated_at        TIMESTAMPTZ
);

CREATE TABLE agent_runs (
  id          TEXT PRIMARY KEY,   -- run_…
  meeting_id  TEXT,    -- mtg_…, NULL for a run with no meeting; the deletion path
  team_id     TEXT,    -- team_…
  requested_by TEXT,   -- user_…, for a chat turn; SET NULL with the user
  trigger     JSONB,   -- why it woke up
  route       TEXT,    -- the subagent it went to, or NULL
  plan        JSONB,   -- what it meant to do
  steps       JSONB,   -- which tools it called, in order
  proposed    JSONB,   -- the plan it submitted for approval (section 8)
  decisions   JSONB,   -- per item: approved | edited | rejected, and the reason
  actions     JSONB,   -- what it actually did
  messages    JSONB,   -- the suspended conversation, for resume (section 8)
  outcome     TEXT,    -- answered | unrouted | budget_exceeded | failed
  answer      TEXT,    -- only when meeting_id is set; see below
  latency_ms  INT,
  token_cost  INT,
  created_at  TIMESTAMPTZ
);

CREATE TABLE agent_approvers (
  team_id     TEXT,    -- team_…
  user_id     TEXT,    -- user_…, ON DELETE CASCADE
  scope       TEXT,    -- research | followup | workload | report | any
  created_at  TIMESTAMPTZ,
  PRIMARY KEY (team_id, user_id, scope)
);
```

**`agent_approvers` is who "the team lead" and "the manager" are.** Follow-up
reports to the lead only and Workload to the manager only (section 3.1), and
nothing in the repository says who either is: `packages/core` has a job role
(`PM`, `Dev`, `Design`, `Data`) and deliberately no administrator. Adding one to
`Team` or `User` would be a shared-entity change owned by module A (invariant
4), for a fact only the agent layer reads. So the layer keeps its own row, set
by the team in the web settings, and a proposal whose scope has no approver is
not sent to anyone — it stays on the run timeline.

**Who may set it, as built (#592).** 설정 › 승인자 (`/settings/approvers`)
writes these rows through `GET`/`PUT /api/agent/approvers`, and the rule is in
`main/approvers.py`. With no administrator in `packages/core`, the layer
answers the question itself: while no current member holds an approver row,
any member may name one, so a new team has a way in; after that only an
approver with scope `any` may change the list; and the team never loses its
last `any` approver, so the list can always be changed again — which makes the
first assignment hold `any`. A former member's rows count for nothing, the same
as in `approver_scopes`. The table holds a role
assignment, not meeting content, so it is deleted with its user rather than with
a meeting.

**Ids are prefixed `TEXT`, in the SQL as well as in the prose.** Primary keys in
this repository are prefixed strings from `autune_core.ids.new_id` —
`data-model.md`'s conventions say so and `ids.py` holds the prefixes — and an
earlier draft of this document asked for that in a paragraph while the `CREATE
TABLE` above it said `UUID`. @kjfcvx12 caught the contradiction; the SQL is what
was wrong. `run_` and `wi_` are two new prefixes, and a prefix constant lives in
`packages/core` (shared, so that one line needs the team's approval like any
other `packages/` change).

**`source_id` and `origin_utterances` exist because a decision is not one
utterance.** A work item derived from B carries B's own `act_…` or `dec_…` id, so
the agent can read the current item rather than its own stale copy, and the
evidence is a list because a decision spans several utterances — an earlier draft
had a single `origin_utterance`, which would have thrown away most of the
evidence for exactly the items that need it most. `origin_utterances` gets a GIN
index; it is the one JSONB column here that is filtered on, which
`data-model.md` allows for that reason.

**`confirmed` is read, never inferred.** It records what the owning module's read
said, and section 8 rule 3 is what depends on it.

`proposed` and `decisions` are the record of the approval gate. They are also
the closest thing this product has to labels that cost nobody anything: a
person approving, editing or rejecting a proposed action is saying something
about whether the agent's judgement was right. What they say is about the
*action* — was this DM worth sending, was this deadline right — and not about
module B's classifier, whose question is whether an utterance was a commitment.
So `decisions` feeds the agent's own ranking and policy (section 8), and it is
a starting point for a labelling session, not a training set. Saying more
would be overclaiming.

**`next_check_at` is the whole of "wakes up by itself".** The scheduler selects
`WHERE next_check_at <= now()` and calls the orchestrator. There is nothing
else to it.

**A run about no meeting keeps no text.** A chat question has no `meeting_id`,
so nothing would delete its row with a meeting it quotes. Its `answer`, and a
proposed action's title and body, go back to the person who asked and are not
stored; the row keeps tool names, evidence ids, the route and the outcome.
`steps` never hold a tool's summary, items or reason, for any run (#449).

**`agent_runs` is written from the first commit, not added later.** Without it
there is no way to answer "why did it do that", which is the only question
anyone asks about an agent — and it is what the run-timeline screen renders.

### How work items get created

Not by the modules. **No module's existing behaviour changes**, and no module
writes an `agent_*` row — that would be a module writing another owner's table,
which invariant 3 exists to prevent. The agent layer subscribes to events those
modules already publish (`autune.extraction.completed`, `autune.gap.completed`,
`autune.context.completed`) and writes its own rows.

The one file each module gains is its own `tools.py`, written by that module's
owner (ADR 0010). "Not modified" means no change to a service, a table, a route
or a contract; it does not mean the module contributes nothing. An earlier draft
said "B, C, D and E are not modified" flatly, which read as though the tools
appeared from nowhere.

**A row created this way starts out unconfirmed, and that is a permission level,
not a note.** `ExtractionResult` is published before anyone reviews it, so an item
born from `autune.extraction.completed` is `confirmed = false` until B's read says
otherwise, and section 8 rule 3 keeps it internal until then. The same applies to
a decision-drift signal from `autune.context.completed`, and rule 5 keeps
`key_stakeholders_absent` out of the row entirely.

Work that was never in a meeting — a Notion task, a request in a channel —
lands in the same table through the same door, with `origin_meeting` and
`origin_module` NULL. That is the moment "beyond the meeting" stops being a
slogan.

### Deletion and retention — both tables, not one

`agent_work_items.body` is derived from utterances, so it is meeting content and
inherits every rule in `privacy.md`: masked before it is written, deleted with
its meeting, and covered by the retention window.

**`agent_runs` is meeting content too, and an earlier draft of this document did
not say so.** Both B and D raised it and D's case is the sharper one.
`agent_runs.steps`, `decisions` and `messages` hold copies of what the tools
returned — B's `description` and `assignee_label`, and D's decision statements.
D's router blanks `previous_statement` and `previous_meeting_id` when the meeting
they quote passes out of the retention window; if the agent's copy is not swept
on the same schedule, **a value D deliberately blanked comes back alive in
`agent_runs`.** And as drafted the table had no `meeting_id` at all, so there was
no path to delete it by meeting — which `privacy.md` section 7's checklist
rejects outright: *"Adds a table with no path to deletion by `meeting_id` or
`user_id`"*. The table above would have been rejected in review, correctly.

So, explicitly:

| Table | Meeting-scoped deletion | Retention expiry |
| --- | --- | --- |
| `agent_work_items` | by `origin_meeting` | yes — `body`, `title`, `external_ref` |
| `agent_runs` | by `meeting_id` | yes — `steps`, `decisions`, `actions`, `messages` |

Both reference `meetings.id` with `ON DELETE CASCADE`, so meeting deletion
reaches them as rows, and both are registered with the retention sweep. A
suspended run whose meeting is deleted or expires cannot be resumed, which is
correct: there is nothing left to act on, and a resume that reconstructed the
deleted content from its own copy would be the defect this rule exists to
prevent. Rule 5 in section 8 keeps one field out of these columns altogether.

`intel_reports` shipped without a deletion path (#86); this must not repeat it,
and a test proving both tables empty after a meeting is deleted is part of
shipping them.

## 6. Triggers

| Kind | Example | Owner | Mechanism |
| --- | --- | --- | --- |
| Time | 09:00 morning briefing | main agent | `@periodic` (#374) |
| Time | 10 minutes before a meeting on the team's Google Calendar | Briefing | `@periodic` poll of the calendar, every minute |
| State | `next_check_at` due; deadline tomorrow and no signal in three days | main agent | `@periodic`, every 5 minutes |
| State | work piling up on one person | Workload | `@periodic`, a few times a day (`Periodic`, #637) |
| Event | a meeting's analysis finished; for Follow-up, whether it left open what the previous meeting also did | Research, Report, Follow-up | `autune.intelligence.completed` |
| Request | "What did we decide about search last week?" | main agent, which may delegate | chat message |

Research does not wake on `autune.transcript.ready`: that event reaches B at the same moment, so B's questions do not exist yet (`agent/docs/specs/2026-09-30-research-subagent-design.md` section 2).

**Every trigger enters through the main agent.** A subagent declares the
triggers it wants (section 3.2) and the main agent's scheduler registers them,
so a subagent owner never writes a `@periodic` task or an event subscription of
their own, and every run — whoever it was for — is one `agent_runs` row with
the same shape.

**Safe to deliver twice.** The trigger's redelivery guard keys on the Celery
task id, so a redelivery of the same task is skipped, while E's re-publish of
`autune.intelligence.completed` is a new task that runs again and supersedes
the older pending proposal of the same subagent for the same meeting.

A periodic task is a `@periodic` declaration beside the task itself
(`async-pipeline.md`, #374); nothing edits `apps/worker`. The agent layer's
tasks are named `autune.agent.periodic.<name>`.

**The brief ten minutes before a meeting is D's surface, and D's owner builds
it.** Module D already owns the pre-meeting brief (`slack.py`, #234) and section
8 rule 2 is that outbound goes out through the module that owns the content.
The Briefing subagent is where the brief is *composed* — it reads B and C as
well as D — and D's surface is where it is *sent*, so one brief goes out,
not two. The earlier "30 minutes" in `prd.md` is now ten.

## 7. The team charter — judgement the team writes down

Gap detection compares a meeting against a domain template. The templates are
YAML reference data (`general.yaml`, `feature_planning.yaml`), a meeting's can
be chosen in S20 (#303), and the comparison is rule-based, so there is nothing
to retrain. What is missing is narrower: **there is no team-level template.**
Every team starts from the same two, and a team's own standard has nowhere to
live.

Coding agents solve the same problem with a file at the root of the project —
a document the team writes in prose, read into the prompt on every run. It is
the cheapest way there is to change behaviour without training, and it moves
the judgement of "what counts as a problem" to the people whose problem it is.

```markdown
# Team charter

## A meeting must settle
- A performance requirement is a number (p95 < 300ms), never "good enough"
- Adopting an external API means naming a cost ceiling and a fallback
- A date is a date with an owner

## Who must be in the room
- Technical spec → the backend lead
- Pricing → the PO

## What we tend to skip
- Error-handling scenarios
- Migration plans

## For the agent
- If an owner is unclear, ask; do not guess
- At most two channel posts a day
```

### How it is used

1. **As input to gap detection — proposed, to be agreed with C.** How a line
   under *A meeting must settle* becomes a template item with keywords and a
   weight is C's design (section 4). Until it is agreed, the charter reaches
   the agent's prompt and policy only.
2. **In the orchestrator's system prompt, on every run.** The judgement is
   present each time a plan is made.
3. **As policy for the action model** (section 8). "At most two posts a day"
   is enforced, not suggested.
4. **As the tuning knob.** When the agent's judgement is wrong for a team, the
   team edits a paragraph.

### Three constraints, because a prompt that drives actions is an attack surface

- **A charter can only tighten.** It may add checklist items, lower a post
  limit, demand an owner. It can never grant a permission level, unblock L3,
  or name a destination. Anything in a charter that reads as an instruction to
  a tool is data for judgement, not an instruction — the same rule the
  orchestrator applies to transcript text.
- **"Who must be in the room" is not checked against who spoke — open.** "The
  backend lead was silent on the spec discussion" is a per-person
  speaking-pattern statement about somebody other than the reader, which
  privacy.md section 3 forbids, and checking it per role does not escape that:
  in a team with one person per role, a role is a person. Nothing fills
  `participants.role` in production today either. Whether this line can be
  checked at all — against attendance rather than speech, or only at a
  team size where a role is several people — is undecided and C's owner's to
  weigh.
- **The charter is stored, versioned and per team.** An `agent_charters` row
  with the text and a version, not a file on a disk somewhere. A run records
  which version it read, because "why did it say that last week" has to be
  answerable.

### Why this matters more than its size

It is a day or two of work — read a document, split it, pass it as a
checklist, prepend it to a prompt — and it changes what the product is. The
onboarding story becomes "write three lines about how your team decides
things". The differentiation becomes concrete: every other meeting tool applies
one template to every team. And it turns low extraction accuracy from an
excuse into a design: the team holds the standard, the system applies it, and
where the standard is unmet the system asks.

It also changes the feasibility of the first scenario (section 10).

## 8. What the agent is allowed to do

An agent that acts will eventually act wrongly. The grades exist before the
first action does.

| Level | Nature | Example | Handling |
| --- | --- | --- | --- |
| L0 | Internal read or draft | Read a module's tool, summarise, draft, write to `agent_*` | Automatic |
| **L0-ext** | **Read that leaves the building** | **The orchestrator's own LLM call; a web search** | **Automatic, through `assert_masked` and the prompt budget — rule 1 below** |
| L1 | Reversible write | Thread comment, agenda draft | Automatic, notify after |
| L2 | Write that moves a person | **Asking the owning module to** DM, post, or create a Notion page | **Plan mode**, then execute |
| L3 | Destructive | Close an issue, delete an event, send externally | Forbidden |

### Six rules, and the L2 row is shorter than it was

Rule 1 is about the one transfer nobody counts as one. Rules 2 to 5 are all the
same rule seen from four sides — **the module that owns the content owns what
happens to it** — which the owners of B and D asked for independently, from
opposite ends of the repository. Rule 6 is not new. They are numbered because a
remark gets read once and a rule gets checked.

#### Rule 1 — every outbound transfer, the orchestrator's own LLM call included, goes through `assert_masked` and a stated budget

The LLM call is the one nobody thinks of as outbound. Every run passes tool
results into `llm(messages, …)`, and B's results carry `description`, which
quotes an utterance, and `assignee_label`, which is a person's name.
@kjfcvx12 is right that this is a transfer and not an internal read, and it is
why `L0-ext` exists as its own row.

**It is not blocked, and it is not new.** `privacy.md` section 6 already
governs it and already permits it: *"Anything leaving our infrastructure — LLM
APIs, Slack, Notion, Jira, Google Calendar, error tracking, analytics — carries
masked text only, and only what the feature needs."* Two conditions, both
already decided. `packages/integrations/src/autune_integrations/privacy.py` is
the single enforcement point, and its own docstring names "any LLM API"
alongside Slack, Notion, Jira and Calendar. So the orchestrator's prompt goes out
through `check_outbound` / `assert_masked` exactly as B's Notion sync and D's
Slack notices do. There is no new mechanism to build and no new decision to
make.

An earlier draft of this document said #92 blocks the layer. It blocks the
design of it no longer, but one of its questions now bears on deployment. #92's
original five are consent surviving a departure, label substitution under PIPA
제36조, the lawful basis for a retained transcript, voice embeddings under
제23조, and GDPR applicability; none of them asks whether content may reach an
LLM API, and the blanket dependency invented here is removed. On 09-28, #392
added a sixth that does: whether a whole meeting's utterances reaching a
provider abroad is an overseas transfer under 제28조의8. It does not change the
mechanism above — `check_outbound` is still the one enforcement point — but it
does set who may be in the meetings we run this on. Section 13.3 states the
rule that holds until #92 answers.

What section 6's *second* condition does impose is a real design constraint,
and it is the one that bites:

| In the prompt | Cap |
| --- | --- |
| Utterance text | **None, by default.** `evidence` is ids (section 4); text is fetched only when a step needs a specific quotation |
| Quoted utterances, when a step needs them | 10, and only from the meeting the step is about |
| Tool results | `summary` plus 5 `items` each, the return contract |
| Meeting-derived content in one prompt | `MAX_OUTBOUND_CHARS` (4,000), the constant `check_outbound` already enforces, and refused past it |

**A transcript never enters a prompt.** A step that needs three action items
sends three action items. This is a budget the loop enforces, not an
aspiration: the return contract already makes it the default, because a tool
hands back a summary and ids rather than rows of text, and `assert_within_size`
already refuses the rest.

The last row needs one clarification, because `MAX_OUTBOUND_CHARS` was written
for a single Slack or Notion message — its docstring says "a single message, not
a transcript". **The cap applies to the meeting-derived part of the prompt, not
to the system prompt and charter**, which are our own text and carry nothing
about a meeting. Whether 4,000 characters of content is the right ceiling for a
multi-step run is the one number in this section that wants measuring against a
real briefing before it is trusted; it is the existing constant until then,
rather than a new one invented here.

One pre-existing limit, stated so nobody reads the above as a promise it does
not make: **a person's name is not in `privacy.md` section 2's masking scope.**
Section 2 covers phone numbers, email addresses, national ID numbers, bank
accounts and card numbers; it does not cover a name or a sentence that
identifies someone by its content, and `find_unmasked` therefore does not catch
either. So `assignee_label` does reach outbound surfaces today. That is true of
B's Notion sync and D's Slack notices as much as of this layer — it is the
system's existing masking scope, not something the agent layer introduces, and
#92 lists it among the things to put to a reviewer.

#### Rule 2 — outbound goes out through whoever owns the content: a module, or the subagent that wrote it

**The agent reads state and puts it in a briefing. It does not send the DM,
create the Notion page, or re-date the item.** Every module already does its
own outbound exactly once and through its own guard, and a second path is a
duplicate message and a bypassed check at the same time:

| Surface | The module that owns it | What it already does |
| --- | --- | --- |
| Ambiguous-agreement confirmation DM | B | `ext_confirmations` + `send_confirmations`, to the speaker only |
| Notion or Jira page for an item | B | once per (item, system) at confirmation time, recorded in `ext_external_refs` (#294) |
| An item's due date | B | `ext_action_items.due_date` is B's column; "re-date" is not the agent's verb |
| Gap report thread, generated question cards | C | `slack.py`; Briefing and Follow-up quote a gap or question into their own output, never post it separately |
| Topic-link notice, decision-drift warning, pre-meeting brief | D | `notify.py`, capped and de-duplicated, implementation in #234 |
| Speaking ratio | E | `feedback.build_speaking_ratio_dm`, DM to the subject only |
| Meeting summary report | E | the Report subagent composes it and proposes two of E's actions: `draft_meeting_report` stores it (L1, in E's `L1_ACTIONS`), and `publish_meeting_report` posts the stored draft once, after approval (L2) |

So an L2 action is never "the agent sends X". It is "the agent asks the owning
module to send X, and the module's own guard decides".

**Content no module owns is owned by the subagent that wrote it**, and that
subagent's owner builds the send — still through `packages/integrations` and
its guard, never a client of its own:

| Surface | Owner | Level |
| --- | --- | --- |
| Research document to the meeting's participants | Research | L2 — an approver with scope `research` |
| A proposed follow-up meeting, as an unconfirmed board item (no calendar event: B's sync adds one after a person confirms it, #441) | Follow-up | L2 — an approver with scope `followup` |
| A proposed redistribution, and any reassignment or message it implies | Workload | L2 — an approver with scope `workload` |

Consequences worth naming:

- **Section 6's ten-minutes-before trigger sends one brief, D's.** The Briefing
  subagent composes it and D's surface sends it. Nothing else posts into that
  slot.
- **"Sent as soon as it exists" means proposed as soon as it exists.** Research
  may finish mid-meeting; its document is on the approver's screen then, and
  goes out on one click. Section 8's rule that a DM is never demoted to L1
  applies to it like any other. `meetings` has no organiser column, which
  is why the approver comes from `agent_approvers` and not from the meeting.
- **The confirmation DM is B's, once.** An agent DMing the same speaker about
  the same utterance is a duplicate, and the speaker cannot tell which of the
  two to answer.

#### Rule 3 — unconfirmed content from B does not reach an outbound surface

`ExtractionResult` is published on `autune.extraction.completed` **before a
person reviews it** (#246, question 2), and B reports model-produced decisions
at roughly 35% precision. A work item built from that event is a candidate, not
a fact, and an unreviewed candidate on a channel post or in a morning briefing
is the product being confidently wrong in public.

@kjfcvx12's rule, adopted as written: **content from B leaves only by way of
B's own review and outbound read path (`GET /reviews/{meeting_id}/outbound`).
An unconfirmed item is L0, internal only.** It may sit in `agent_work_items`,
appear on the run timeline and be shown to the person who opens the review
screen. It may not be summarised into anything that leaves.

This is #246/#247's gate, and the agent honours it by reading through it rather
than around it.

#### Rule 4 — how the agent learns that an item is confirmed: it asks B

**Contracts stay unchanged, and this is how.** `Decision` carries no review
state, and confirming or `PATCH`ing a decision publishes no event, so "the
contracts are unchanged" needed a mechanism rather than an assertion —
@kjfcvx12 was right to ask which of two it would be.

The answer is the first one: **the agent calls B's read tool and reads the
review state from the response.** No optional field on `Decision`, no
`autune.extraction.reviewed` event. `packages/contracts` is frozen and
additive-only after W1 (invariant 5), and a change there needs every affected
owner's approval and a version bump — for a fact one read already returns. The
cost is a call per run instead of a push, which the 15-call budget in section 9
absorbs. If a later feature genuinely needs the push, that is an additive
contract change with its own issue and its own approvals, not something this
document decides in passing.

#### Rule 5 — `key_stakeholders_absent` is never stored and never forwarded

D holds "which stakeholders were absent from this decision" out of its read API
deliberately: `DecisionVersionRead`'s docstring says so, #188 is the exposure
it avoids, and it goes back in only after #156 puts authentication on
`/api/context`. The value travels on exactly two paths — the `ContextLinks`
event (`autune.context.completed`) and a decision-drift DM to the absent person
themselves. Even D's own team-channel notice states a count and no names.

The agent subscribes to `autune.context.completed` (section 5) and persists
resume messages to `agent_runs` (section 8), so both of those paths lead
somewhere this value must not go. **The agent does not store
`key_stakeholders_absent` in any `agent_*` column, does not put it in a prompt,
and does not put it in a briefing.** One person's record of which cross-meeting
decisions they missed is theirs, exactly like a speaking ratio, and
`assert_personal_delivery` is the existing mechanism for this shape: subject
only, direct only, no administrator override. A drift warning the agent wants to
raise goes out as D's DM, by rule 2.

#### Rule 6 — L3 has no exception

Unchanged, and stated here so the six read together.

### Plan mode — a plan is submitted before anything at L2 happens

**As built (`agent/docs/specs/2026-09-30-plan-mode-design.md`): an approval queue.** Every subagent plans inside its own graph and returns `ProposedAction`s, so there is no model loop to add; an L2 proposal waits in `agent_pending_actions` until an approver with its scope approves it on the approvals page, and then runs under its run's scope. The loop below stays as the direction if a subagent ever needs the model to plan; it would need an answer for keeping `messages`, which #509 does not store. Approval is at-most-once: the claim is committed before the action runs, so an approval interrupted mid-run reads approved with no result and is never re-run — other modules' writes commit in their own sessions, and running one twice would move a person twice. An approval interrupted mid-run is listed on the approvals page as needing a check, not hidden. Only a current member of the team who holds an approver row for the proposal's scope, or `any`, may decide.

Borrowed whole from coding agents. It is not a second model or a planning
algorithm; it is the same loop with three differences:

1. **The write tools are not in the tool list.** While planning, `Comms` does
   not exist. The agent can only read.
2. **One paragraph is added to the system prompt**: you are planning; when the
   plan is complete, submit it with `submit_work_plan`.
3. **Submitting the plan is itself a tool call**, and it is how the planning
   phase ends.

```python
mode = "plan"
while True:
    tools = READ_ONLY + [submit_work_plan] if mode == "plan" else ALL
    response = llm(messages, tools=tools, system=base + charter + (PLAN if mode == "plan" else ""))
    for call in response.tool_calls:
        if call.name == "submit_work_plan":
            decisions = ask_person(call.args["actions"])      # per item: approve / edit / reject
            approved = [a for a, d in zip(call.args["actions"], decisions) if d.ok]
            if approved:
                mode = "execute"                              # same messages, tools unlocked
            messages.append(tool_result(approved=approved, rejected=[…]))
            continue
        messages.append(execute(call))
```

Twenty or thirty lines around a `mode` variable. Three things it buys, and the
third is the one that matters:

- **A gate before the irreversible.** Obvious, and the least of it.
- **The plan becomes context.** A plan the agent wrote itself sits in the
  messages and every later turn refers to it. Without one, the third action
  has forgotten the first intention.
- **Without write tools the agent does not rush.** With a write tool available
  the agent acts at sixty percent understanding, and that action stays in the
  context as a premise for everything after. Take the write path away and
  reading is all there is, so it reads more. Removing the ability to act
  raises the quality of the investigation.

**The plan is the work-item draft.** `submit_work_plan` takes a list of
`ProposedAction` — kind, title, body, owner, due date, the tool call to make,
the level, the rationale, the evidence — and an approved action is inserted
into `agent_work_items` as it is. The approval gate and the state store are
one mechanism seen from two sides.

**Per-item approval is required.** A coding agent approves a plan whole; a
team approves three of five actions and rejects two with a reason. The reason
goes to `agent_runs.decisions` and is in the prompt the next time the same
kind of situation comes up.

### Waiting for a person — without a Celery task waiting

The approval can take hours. A Celery task cannot sit blocked for hours, and
must not: `acks_late` redelivers it, a worker restart loses it, and a queue
that holds suspended work is a queue nobody can drain. So the wait is not
inside the task. On `submit_work_plan` the run **persists its messages and the
proposal to `agent_runs` and ends**. The decision arrives as an event
(webhook, button, web form), and a new task **loads the messages back and
resumes in `execute` mode**. The context the plan was made in is the messages;
the messages are rows; nothing is lost by the task ending. Enqueuing that
resuming task from the API process works today (#258, closed by #300); waking
one on a timeout instead of on a person's click is #207's beat schedule.

**An L2 proposal's arguments are ids and short scalars only** — every key is a short lowercase name (`[a-z_]{1,32}`), and every value an id, an ASCII ISO date, a boolean, or a lowercase enum up to 32 characters. Text a proposal needs is stored by its owner first and pointed at by id (E's report draft, Research's document). Anything else is refused and not queued.

### When it asks, and when it does not

**Plan mode runs only when the plan contains at least one L2 action.** A run
that ends at L0 and L1 — research, a summary, a thread comment, an internal
write — executes and notifies afterwards. An assistant that asks about
everything is an approval workflow, not an assistant, and the morning briefing
(section 10) is the demonstration that it does not ask.

Two rules soften the gate without removing it:

- **Repeated approval proposes demotion.** The same `(team, kind, action_type)`
  approved five times in a row proposes moving that type to L1, and a person
  confirms. It is per team, reversible, and **never applies to a direct
  message**: a DM moves a person by definition, and an assistant that starts
  DMing without asking after five yeses is the notification bot this design
  exists to not be.
- **The charter's limits are policy** — "two posts a day" is checked before
  the plan is submitted, not after.

There is no urgent exception. L3 is never executed.

Approvals arrive as buttons, on a web screen first and in Slack second: every
module's Slack handler is still a TODO and #80 has not decided whether Slack
is required at all, so a web approval page in `features/` is the path that
does not wait on anyone.

`L0-ext` is separated from `L0` deliberately. A prompt or a search query built
from meeting content *is* an outbound transfer, and invariant 11 does not
distinguish between a transfer made to be helpful and any other. What separating
it buys is that the guard and the budget in rule 1 apply to a named row rather
than to an intention.

## 9. The context budget

Most of the time spent building an agent goes to deciding what goes into the
context and what stays out — not to the model. The numbers are fixed here so
they are a decision and not a discovery.

| What | Cap | Order |
| --- | --- | --- |
| Open work items loaded per run | 30 | deadline soonest, then escalation highest, then most recent signal |
| Past meetings | 3 | D's stored links for this meeting, ranked by D, not by recency |
| Each tool result | `summary` + 5 `items` | the return contract, section 4 |
| Tool calls per run | 15 | past it, stop and hand over with the partial trace kept |
| Tokens per run | a ceiling, then observed in `agent_runs.token_cost` | cost has to be predictable before it can be reduced |
| Wall clock per run | 2 minutes | |

**The caps are per run, and a delegation is part of its parent's run.** When
the main agent hands a task to a subagent, the subagent's tool calls, tokens
and wall clock count against the same run, and the subagent returns a
`ToolResult` (section 3.2) rather than its whole conversation — that is what
keeps five subagents from filling the main agent's context. A subagent may set
tighter caps for itself; none may raise them.

**The agent does not choose the three past meetings by searching.** Cross-meeting
links are computed at pipeline time and stored in D's tables; `links_for_meeting`
reads them and the agent takes the top three D ranked. An earlier draft said
"chosen by D's search tools", which assumed a search route D does not have
(section 4).

When a cap is hit the run stops and says so. It does not summarise its way
past the cap, because a summary of a truncated context is a confident answer
to a question that was not fully read.

## 10. Acting on tools that are not reliable yet

This is the part of the design worth defending, and it is not a caveat.

Module B's classifier scores five-way macro F1 0.225 on English AMI at the
meeting's real distribution, with 1,888 false labels per 2,400 utterances
(#149), and catches 17% of ambiguous agreements (#115). **That is an AMI number
and not a Korean one.** There is no agreed Korean evaluation set yet (#10) and
`AUTUNE_EXTRACTION_CLASSIFIER_CHECKPOINT` stays blank until one exists, so no
Korean figure can be quoted here — an earlier draft of this document read 0.225
as "a real meeting distribution" as though it were ours.

Module C's **four steps all produce values**: relation extraction (#249),
template comparison (#266), question generation (#291) and the S20 checklist
(#303) are on `main`, and `detect_gaps` stores each gap with a `risk_score` and
a `suggested_question` in `gap_gaps`, so `GapReport.gaps` is filled. What is
left is measuring precision on real meetings (#22). Earlier drafts of this
document said C's risk scoring did not exist; that was a week out of date.

An agent built on the assumption that its tools are right would be confidently
wrong several times per meeting. So the loop treats confidence as a first-class
input:

- **The gate reads the owning module's own confidence and review state. It does
  not invent a constant.** For an item from B that is `candidate_confidence`
  — a config value B deliberately leaves unset (`None`) because nothing has
  measured one (#10) — together with whether the item has been reviewed. An
  earlier draft of this document wrote `confidence >= 0.5`, which is a number
  nobody measured; B's `confidence` is an uncalibrated softmax maximum, not the
  margin over the runner-up, so a threshold on it does not mean what a reader
  would assume it means. While B's threshold is unset, nothing from B is
  asserted and everything from B is a candidate, which the next rule already
  handles.
- **Under the gate: do not act. Ask a person**, quoting the evidence, and
  record the answer on the work item.
- A tool returning `ok=False` is a fact to route around, not an error to retry
  blindly.

`agent_work_items.confidence` therefore stores what the module reported, with
the module and the field it came from, and never a value the layer computed.

The honest version of this product is not one that hides a weak extractor
behind a confident assistant. It is one that knows which of its own senses to
trust and says so — and that is a better story than "we built an agent",
because it is a harder thing to build.

### What the charter does to the first scenario

The scenario an earlier draft led with — an ambiguous agreement ("that
performance is probably fine") caught after the meeting, researched, and put
to the owner as a choice — leans on the weakest point in the repository: the
ambiguous-agreement classifier catches 17% (#115). C is no longer the weak
point — its gaps carry risk scores and suggested questions — so the scenario
can take the gap side from C as it is.

What the charter would add — "a performance requirement is a number" checked
against a meeting — is the proposal in section 4 and is C's to design. C's
comparison uses no model by design (`detect.py`), so putting an LLM judgement
inside C is C's decision, not this document's.

So the recommendation is: **both scenarios, in this order.** The morning
briefing first, because it runs on modules that exist and it demonstrates the
loop *not* asking. The ambiguous-agreement scenario second,
because it demonstrates plan mode and the charter together, and it is the one
that shows what the product is for. The W4 gate cuts the second, never the
first.

## 11. Two experiments that the structure makes cheap

Tools are swappable behind one interface, so the same evaluation set can be
run through more than one implementation of the same tool.

| Track | The model behind utterance classification | Measured |
| --- | --- | --- |
| T1 | Module B's classifier (DeBERTa) | macro F1, latency, cost |
| T2 | An LLM with a prompt | same |
| T3 | B's classifier as a first pass, an LLM on the uncertain band | same |

**T2 and T3 run in process or on our own inference server. A third-party API is
not one of the options.** Feeding a meeting's utterances to a classifier means
feeding *all* of them, which fails `privacy.md` section 6's second condition —
only what the feature needs — no matter how well the first one is met. Module B
already closed this door on purpose: `pipeline/base.py` states that no
implementation there sends an utterance to a third party and that adding an
`external` option "would be a privacy decision rather than a config string", and
`pipeline/registry.py` lists `local`, `hosted` and `fake` with the same note.
This experiment does not reopen it. `hosted` — our own server — is what T2 and
T3 mean.

**Stale since #393, and not this document's call to settle.** Module B has
since added `classifier_impl=llm`, which sends masked utterance windows to
Gemini through `check_outbound`, so the door the paragraph above describes is
open in B's own code. Whether that meets section 6's second condition is a
team decision on #392 (`decision`, `privacy`) rather than B's owner's alone,
and it is still open; until it closes, `llm` runs on demo meetings only. This
section keeps the old wording only so the history reads straight, and T2 now
has a running implementation to measure.

**One task, not three.** Utterance classification has a measured baseline (#149)
and an evaluation set. Gap detection has an implementation but no labelled
gaps to measure it against yet (#22). Topic linking is the second task and is
**ready now**: D's harness merged to `main` with #240 (`e15ded1`), carrying both
the `topic_linking_v1` and `decision_lineage` evaluation sets, so an earlier
draft's "when that harness merges" is stale. Writing a three-by-three table
before its rows can be filled is a promise the numbers may not keep.

The second experiment is cheaper and more telling: **the same gap check with
and without the charter.** If a paragraph a team wrote moves the F1 of gap
detection more than a retraining would, that is the product argument in one
row.

A failed combination is recorded like a successful one. The point of the
table is what was measured, not what worked.

## 12. Deliberately not in this

- **A per-module subagent for A, B, C, D and E.** Context isolation is the
  return contract; a loop in front of a single tool call is cost without
  benefit. The five subagents are per *feature*, and each spans modules.
  Section 3.
- **A subagent calling another subagent.** Delegation goes through the main
  agent, one level deep. Section 3.2.
- **Open-web search in Research, for now.** Research reads what the team already holds; uploaded material has no store yet.
  Section 13.3.
- **The agent sending anything itself.** Outbound goes out through whoever owns
  the content — a module, or the subagent that wrote it — and always through
  `packages/integrations`. Section 8 rule 2.
- **A classifier behind a third-party API.** Section 11.
- **A charter that can grant anything.** It tightens only. Section 7.
- **LangGraph's persistence and prebuilt tools.** The graph is LangGraph; its
  checkpointer tables and its outbound tools are not. Section 3.3.
- **Replacing the fixed pipeline.** It stays. The agent is an added path, and
  the demo has a version that does not need it.
- **Autonomy over external systems.** Everything at L2 waits for a person.

### Limits the loop enforces on itself

The context budget, section 9. Past any cap the run stops and hands over to a
person, with the partial trace kept in `agent_runs`.

## 13. Open questions

13.1 and 13.2 blocked the first line of code and are answered. 13.3 to 13.6
shape the work without blocking it.

### 13.1 Where does the layer live? — answered: top-level `agent/`

`packages/agent/` breaks the import-linter contract *Packages do not depend on
modules*. `apps/agent/` breaks invariant 6, *apps is assembly only*. A new
top-level `agent/` breaks neither but adds a layer. The owners of B, C, D and E
each agreed to the third on #260; ADR 0010 records it and moves to `Accepted`
when #261 merges.

### 13.2 How is a periodic trigger registered? — answered: `@periodic` (#374)

A task declares its own period with `@periodic` beside `@shared_task`, and
`make_celery_app` builds the beat schedule from the task registry
(`async-pipeline.md`). Nobody edits `apps/worker`. Every time and state trigger
in section 6 stands on this, and so does plan mode's resume on a timeout.

### 13.3 Which outbound providers, on what terms — #392's rule until #92 answers

**The rule is decided; the vendor list is not.** `privacy.md` section 6 says
what may cross the boundary — masked text, only what the feature needs — and
`packages/integrations/privacy.py` enforces it for every destination including
"any LLM API". Section 8 rule 1 applies that to the orchestrator's own LLM call
and states the prompt budget.

An earlier draft said #92 does not touch this. That was true of #92's original
five questions — consent surviving a departure, label substitution under PIPA
제36조, the lawful basis for a retained transcript, voice embeddings under
제23조, GDPR applicability — and it stopped being true on 09-28, when #392 added
a sixth: whether sending a whole meeting's utterances to an LLM provider abroad
is an overseas transfer under 제28조의8. Names are why — `find_unmasked` carries
no name pattern, so a real name spoken in a meeting crosses the boundary, for
every utterance rather than a few sentences. That question is open on #92, and
the rule below is what holds until it is answered.

What is undecided is narrower than the architecture: **which providers we send
to, and under what agreement.** Two parts, and each carries a condition:

- **The LLM provider: Gemini, on the same terms as B's `classifier_impl=llm` —
  demo meetings only until #392 is decided and a paid key with recorded
  data-processing terms replaces the free one.** Nothing in the code tells a
  demo meeting from a real one, nor a free key from a paid one (#405), so this
  is a deployment rule and not a runtime check. Module B already calls Gemini
  through `check_outbound` (#393), so the agent layer adds no new provider;
  what is still owed is those terms, recorded next to the credential in
  `../engineering/environments.md` as for every third-party integration, and
  the free tier's rate limit — B hit it (#419) — which five subagents sharing
  one key will hit sooner. It does not block design or the mock-tool milestone.
- **Open-web search.** This one stays out of scope for the release. A search
  query *is* the payload — there is no feature-scoped subset of it to send the
  way there is for a prompt — and a general search engine is not a processor we
  have terms with. So **Research reads only what the team already holds** — its own meetings today, uploaded material once there is a store for it — which is enough
  for the scenario in section 10 and asks nothing of anyone.

Research changes when that call happens. With the agent layer on
(`router_impl=gemini`, the default) and a key configured, **every analysed
meeting now sends its raised questions and the matching past utterances to the
model automatically**, through Research on `autune.intelligence.completed` —
not only when someone chats. What is sent is masked text plus each quoted
meeting's title and date; the speaker label is stripped before the prompt is
built, so no speaker name is sent. The rule above applies unchanged: until #392
is decided and the key is paid, demo meetings only.

One thing is worth restating rather than rediscovering: `privacy.md` section 2's
masking scope does not include a person's name, so a name does reach every
outbound surface we already have. That is pre-existing, applies equally to B's
Notion sync and D's Slack notices, and is on #92's list of things to put to a
reviewer. It is not a property of this layer and not a reason to hold it.

### 13.4 Where the charter lives, and who may edit it

A charter drives judgement and policy, so who can change it is a permission
question: a team admin, any member, a reviewer? `agent_charters` needs an
owner column and a version, and the answer decides whether a charter edit is
an L1 or an L2 action of the person making it. Not decided.

### 13.5 Who sets the approvers

`agent_approvers` (section 5) says who the lead and the manager are, and a
wrong row sends a workload proposal to the wrong person. Setting a row is
therefore itself a permission question, the same one as 13.4, and should be
answered with it. Not decided: until then nothing in the product sets a row —
no endpoint, no screen — and the rows are seeded with SQL for the demo.

For the demo: `INSERT INTO agent_approvers (team_id, user_id, scope) VALUES ('<team>', '<user>', 'any');`

### 13.6 Gmail is a new integration, and Jira is back

Workload's owner builds the mail side. `packages/integrations` has Slack,
Notion, Jira and Calendar clients and no mail client; a new one is a
shared-package change (invariant 10) with the team's approval, and it goes
through `privacy.py` like every other client. Mail is also the one surface
that reaches people outside the team, which is L3 in section 8 today — so the
first version reads mail and drafts replies, and sends nothing.

Jira is a working client again: it was evaluated and dropped (#82) and brought
back over OAuth 3LO (#457, #458), with 강민구 owning it. Briefing's issue list
is in — B's `TeamAgenda` (#436), read through D's `brief_agenda`. A Jira read
by Workload is not built, and Workload does not depend on one.

### 13.7 A republished event reruns the subagents it wakes

`autune.intelligence.completed` is published more than once for a meeting: E
re-aggregates when a late module reports, and again when C republishes its
`GapReport` after a dismissal, a template switch (#498) or a rescoring (#506).

Settled with plan mode (#556, confirmed on #571): `_already_ran`
(`main/triggers.py`) skips a run by its **Celery task id**, not by the event.
A redelivered message carries the same task id and is skipped; E's republish
is a new task, so every subagent woken by the event -- Report, Research,
Follow-up -- runs again on the corrected result. When a new run queues its
proposals, `pending.queue_l2` marks the same team's, meeting's and subagent's
earlier `pending` rows `superseded`, so a stale card leaves the queue
(`test_triggers.py::test_the_same_task_is_skipped_and_a_new_task_runs_again`).

The Report's post is pinned to its own run's draft (review of #508). Both
proposals carry one `draft_id`, E stores it with the draft, and
`publish_meeting_report` and the delivery task post only that draft --
approving a proposal whose draft a later run has replaced posts nothing
(`draft not current`). A rerun therefore cannot change what an earlier approval
posts. The approval card reads the draft through E's
`intelligence.meeting_report_draft(team_id, meeting_id, draft_id)`, which
returns the text only while that draft is the stored one (#571).

## 14. Build plan — from 2026-09-29 to 2026-10-12

The mentor's dates on #260: the base features run end to end by **9/30**, and
development closes on **10/12**. The agent layer fits between them. Each row is
one owner's; a date is when it is merged, not started.

**Agent code merges after this document.** #260 is decided and ADR 0010 is
`Accepted` here; the skeleton (#432) merges once this does.

| By | Main agent (김민경) | Every subagent owner |
| --- | --- | --- |
| **10/1** | `agent/` skeleton merged (#432): workspace member, the fourth to sixth import-linter contracts, `ToolResult`, `Subagent`, the registry, a supervisor graph running one mock subagent over mock tools | confirm with 강민구 what your subagent needs from Calendar or Jira; open an issue for anything missing |
| **10/5** | `agent_work_items`, `agent_runs`, `agent_approvers` and their migration; the chat endpoint; the run-timeline screen | your module's `tools.py` returns real data; your subagent runs against mock tools with its own tests |
| **10/9** | plan mode and the approval screen; triggers from section 6; the morning briefing | your subagent runs against real tools, end to end on one real meeting |
| **10/12** | demo run of all five subagents; the fixed pipeline still works with the layer off | fixes only |

**Gate on 10/9.** A subagent that has not run end to end on a real meeting by
then is left out of the demo, and the demo shows the rest. The chat answer and
the morning briefing are the minimum; if those do not run, the fixed pipeline is
what gets presented. One finished thing beats five half-built ones.

**How to start a subagent.** Read sections 3.1 to 3.3 for the shape, section 4
for the return contract, section 8 for what you may send, and section 9 for the
budget. Build against the mock tools the 10/1 skeleton ships, so you are not
waiting on anyone's module. Put your dependencies in `agent/pyproject.toml`
only if the main agent does not already have them, and say so in the PR.

---

## Appendix — where each pattern comes from

Every mechanism above is one that a working coding agent already uses. That is
the design's justification: nothing here is invented for the demo.

| In a coding agent | Here | Why it transfers |
| --- | --- | --- |
| A `while` loop and function calling | The orchestrator, section 3 | There is no planner module; the model reads a result and decides the next call |
| `grep` and `read`, not an index | A module's own reads, exposed one per question, section 4 | A tool per question the module can already answer beats one tool with a mode flag |
| A rules file at the project root | The team charter, section 7 | The cheapest way to change behaviour without training |
| Reading intent (issues, docs) against reality (code, tests) | Charter against transcript = gap detection | "What should happen next" is the difference between the two |
| Subagents for context isolation | The return contract, section 4, and one subagent per feature, section 3 | A subagent returns a summary, not its conversation; it earns its own loop only when a task needs several reads and a judgement between them |
| Plan mode | `submit_work_plan` and the gate, section 8 | Removing write tools raises the quality of the investigation |
| A todo-list tool | `agent_work_items`, section 5 | In a domain with no codebase, the state has to be built |
| An execution trace | `agent_runs`, section 5 | Without observability there is no debugging and no trust |

Related: `module-boundaries.md`, `async-pipeline.md`, `data-model.md`,
`privacy.md`, `../decisions/0010-agent-layer-placement.md`, `../product/prd.md`.
