# The agent layer

> **Status: Proposed.** Nothing described here is built. The direction is under
> discussion in issue #260 and the layer's location is ADR 0009, still
> `Proposed`. Read this as a design under review, not as how the system works.
> Three questions in section 10 block the first line of code.

Modules A–E are exposed as **tools**. An agent layer above them decides which
tools to call, when to wake up, and what it is allowed to do with the answer.

This document covers only the layer. Module boundaries
(`module-boundaries.md`), contracts (`contracts.md`) and the event pipeline
(`async-pipeline.md`) are unchanged by it, and that is the point.

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
  the primary risk control in section 9.
- Invariant 2. Modules still may not import one another.

**Changes:**

- Each module gains one new file, `modules/<name>/src/autune_<name>/tools.py`,
  listing three to five callable tools. Nothing else in the module is touched.
- A new layer, location pending ADR 0009, holds the orchestrator, the tool
  registry, the work-item store and the triggers.
- Two new tables with an `agent_` prefix (section 5, and `data-model.md`).

## 3. Shape

```
  triggers                ┌──────────────────────────────────┐
  ─────────               │       Orchestrator               │
  scheduler ─────────────>│   sense → plan → act → log       │
  meeting completed ─────>│   "what should happen now?"      │
  user request ──────────>└──────────────┬───────────────────┘
                                         │
                       ┌─────────────────┴──────────────────┐
                       │                                    │
                       ▼                                    ▼
              tool registry                           Research agent
                       │                            (LLM + web search)
        ┌──────┬───────┼───────┬────────┐                   │
        ▼      ▼       ▼       ▼        ▼                   ▼
      [A]    [B]     [C]     [D]      [E]           integrations/privacy
       └──────┴───────┴───────┴────────┘             (outbound boundary)
            modules, unchanged
                       │
                       ▼
            agent_work_items · agent_runs
```

Note what is *not* in the diagram: a per-module subagent. An earlier draft had
six subagents, one wrapping each module. Four of them would have had no
behaviour of their own — they would call one module and return. The
orchestrator calls those tools directly, and only **Research** exists as a
separate agent, because it is the one that reasons over several sources and is
the one that talks to the outside world.

## 4. Tools — how a module becomes callable

A module owner adds one file and changes nothing else:

```python
# modules/gap/src/autune_gap/tools.py
from autune_agent.tools import tool


@tool
def detect_gaps(session: Session, meeting_id: str, template: str = "auto") -> GapReport:
    """Find what this meeting should have covered and did not.

    Use this after a meeting has been transcribed, or when asked which
    discussions a meeting missed. Do not use it to find what *was* said —
    that is `list_action_items`.

    Returns GapReport(gaps=[{topic, missing_field, risk_score, suggested_question}]).
    """
    return service.detect_gaps(session, meeting_id, template)


TOOLS = [detect_gaps, build_topic_graph, score_risk]
```

Collected the way `apps/api` collects routers — by iterating the module list,
never by appending to a registry (invariant 6):

```python
for name in MODULES:
    for tool in import_module(f"autune_{name}.tools").TOOLS:
        REGISTRY[tool.name] = tool
```

### Rules for a tool

1. **Synchronous, with type hints.** Arguments and return values are
   `packages/contracts` models. *Not* `async` — this repository is synchronous
   SQLAlchemy and synchronous routes throughout, and an `async def` wrapping a
   blocking call is a lie that costs a thread.
2. **The docstring is the prompt.** Say **when to use it**, and when not to,
   before saying what it does. Half of an agent's accuracy is decided here.
3. **Safe to call twice**, with one recorded exception:
   `autune.audio.process_recording` is not, and cannot be. It deletes the
   recording in a `finally` (invariant 11), so a second call has nothing left
   to decode. Module A exposes reads and the transcript, not a re-transcribe.
4. **Thirty-second budget.** Past it, return partial results with
   `partial=True` rather than blocking the loop.
5. **Never raise for an expected failure.** Return `ToolResult(ok=False,
   reason=...)` so the agent can read it and take another route. Reserve
   exceptions for bugs.

Three to five tools per module. Finer than that and the agent gets lost.

## 5. State — the two tables that make it proactive

The repository has action-item cards. It has no table holding **what is true
about a piece of work right now**, across meetings. That single absence is
what makes the product reactive.

```sql
CREATE TABLE agent_work_items (
  id                UUID PRIMARY KEY,
  kind              TEXT,       -- action | gap | open_question | decision | risk
  title             TEXT,
  body              TEXT,       -- masked, like everything derived from a transcript
  origin_meeting    UUID,       -- NULL when it was not born in a meeting
  origin_utterance  UUID,       -- the evidence for why this exists
  owner_id          UUID,
  due_date          DATE,
  status            TEXT,       -- open | in_progress | blocked | resolved | dropped
  confidence        FLOAT,      -- see section 8
  external_ref      JSONB,      -- {"jira": "PROJ-123", "notion": "..."}
  last_signal_at    TIMESTAMP,
  escalation_lv     INT,        -- 0 watch · 1 DM · 2 raise on agenda · 3 report to lead
  next_check_at     TIMESTAMP   -- when the agent wakes itself for this item
);

CREATE TABLE agent_runs (
  id          UUID PRIMARY KEY,
  trigger     JSONB,   -- why it woke up
  plan        JSONB,   -- what it meant to do
  steps       JSONB,   -- which tools it called, in order
  actions     JSONB,   -- what it did, or is waiting for approval to do
  outcome     TEXT,
  latency_ms  INT,
  token_cost  INT
);
```

**`next_check_at` is the whole of "wakes up by itself".** The scheduler selects
`WHERE next_check_at <= now()` and calls the orchestrator. There is nothing
else to it.

**`agent_runs` is written from the first commit, not added later.** Without it
there is no way to answer "why did it do that", which is the only question
anyone asks about an agent — and it is what the run-timeline screen renders.

### How work items get created

Not by the modules. **B, C, D and E are not modified.** The agent layer
subscribes to events those modules already publish
(`autune.extraction.completed`, `autune.gap.completed`,
`autune.context.completed`) and writes its own rows. A module writing to
`agent_*` would be a module writing another owner's table, which invariant 3
exists to prevent.

Work that was never in a meeting — a Jira issue, a request in a channel —
lands in the same table through the same door. That is the moment "beyond the
meeting" stops being a slogan.

### Deletion and retention

`agent_work_items.body` is derived from utterances, so it is meeting content
and inherits every rule in `privacy.md`: masked before it is written, deleted
with its meeting, and covered by the retention window. It registers with
`autune_core.deletion` like any other derived table. `intel_reports` shipped
without that path (#86); this must not repeat it.

## 6. Triggers

| Kind | Example | Mechanism |
| --- | --- | --- |
| Time | 09:00 team briefing; 30 minutes before a meeting | Celery beat |
| State | `next_check_at` due; deadline tomorrow and no signal in three days | 5-minute poll |
| Event | Meeting analysis finished; Jira status changed; bot mentioned | Existing events + webhooks |
| Request | "Summarise last week's decisions" | Slash command |

For the first release, **event + state** is enough. Both depend on the
question in section 10.2: there is no Celery beat in this repository yet, and
a module cannot add one.

## 7. What the agent is allowed to do

An agent that acts will eventually act wrongly. The grades exist before the
first action does.

| Level | Nature | Example | Handling |
| --- | --- | --- | --- |
| L0 | Internal read or draft | Search, summarise, draft, write to `agent_*` | Automatic |
| **L0-ext** | **Read that leaves the building** | **Web search, LLM provider call** | **Section 10.3 — unresolved** |
| L1 | Reversible write | Thread comment, agenda draft | Automatic, notify after |
| L2 | Write that moves a person | DM, channel post, Jira create or re-date | Approval, then execute |
| L3 | Destructive | Close an issue, delete an event, send externally | Forbidden |

Approvals arrive as buttons. **Refusals are recorded in `agent_runs.actions`
and fed back into the next decision** — the cheapest possible version of an
assistant that learns from its team.

`L0-ext` is separated from `L0` deliberately. A web search built from meeting
content *is* an outbound transfer, and invariant 11 does not distinguish
between a transfer made to be helpful and any other.

## 8. Acting on tools that are not reliable yet

This is the part of the design worth defending, and it is not a caveat.

Module B's classifier scores macro F1 0.225 on a real meeting distribution
(#149) and catches 17% of ambiguous agreements (#115). Module C's pipeline
does not yet produce a value at all. An agent built on the assumption that its
tools are right would be confidently wrong several times per meeting.

So the loop treats `confidence` as a first-class input:

- `confidence >= 0.5` — act within the permitted level.
- `confidence < 0.5` — **do not act. Ask a person**, quoting the evidence, and
  record the answer on the work item.
- A tool returning `ok=False` is a fact to route around, not an error to retry
  blindly.

The honest version of this product is not one that hides a weak extractor
behind a confident assistant. It is one that knows which of its own senses to
trust and says so — and that is a better story than "we built an agent",
because it is a harder thing to build.

## 9. Deliberately not in this

- **A per-module subagent for A, B, C, D and E.** They would be wrappers with
  no behaviour. Section 3.
- **A framework.** A hand-written loop plus function calling, on the order of
  200 lines. A graph library makes this harder to debug and harder to explain,
  and explaining it is half the value.
- **Replacing the fixed pipeline.** It stays. The agent is an added path, and
  the demo has a version that does not need it.
- **Autonomy over external systems.** Everything at L2 waits for a person.

### Limits the loop enforces on itself

Fifteen tool calls per run, a token ceiling, and a two-minute timeout. Past any
of them the run stops and hands over to a person, with the partial trace kept
in `agent_runs`.

## 10. Open questions — these block the work

### 10.1 Where does the layer live? — ADR 0009

`packages/agent/` breaks the import-linter contract *Packages do not depend on
modules*. `apps/agent/` breaks invariant 6, *apps is assembly only*. A new
top-level `agent/` breaks neither but adds a layer. Proposed: the third.

### 10.2 How is Celery reached outside the worker? — #258, #207, #227

There is no beat schedule, the API process has no Celery app at all, and
modules may not edit `apps/worker`. Every trigger in section 6 stands on this,
and so does the upload endpoint that already shipped. Three issues, one
question; they should be decided together.

### 10.3 May meeting content leave the building? — #92

The Research agent searches the web and calls an LLM provider. Both are
outbound transfers of content derived from a transcript. `privacy.md` and
`packages/integrations/privacy.py` define the boundary; what may cross it, even
masked, has not been decided. Until it is, Research runs against uploaded
material only, never the open web.

---

Related: `module-boundaries.md`, `async-pipeline.md`, `data-model.md`,
`privacy.md`, `../decisions/0009-agent-layer-placement.md`, `../product/prd.md`.
