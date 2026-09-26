# The agent layer

> **Status: Proposed.** Nothing described here is built. The direction is under
> discussion in issue #260 and the layer's location is ADR 0009, still
> `Proposed`. Read this as a design under review, not as how the system works.
> Two questions in section 13 block the first line of code: where the layer
> lives (13.1) and how a periodic trigger is registered (13.2). 13.3 and 13.4
> shape the work without blocking it.

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
                       │                        (LLM + uploaded material)
        ┌──────┬───────┼───────┬────────┐                   │
        ▼      ▼       ▼       ▼        ▼                   ▼
      [A]    [B]     [C]     [D]      [E]           integrations/privacy
       └──────┴───────┴───────┴────────┘             (outbound boundary)
            modules, unchanged                    every prompt and every
                       │                          message, section 8 rule 1
                       ▼
            agent_work_items · agent_runs
```

Note what is *not* in the diagram: a per-module subagent. An earlier draft had
six subagents, one wrapping each module, and gave the right reason for them —
**context isolation**: a gap detector that reads three hundred utterances and
finds forty-seven gaps must not hand all forty-seven to the orchestrator, or the
orchestrator has no room left to think. That reason is right and the mechanism
is wrong. Isolation is a property of what a tool *returns*, not of whether an
LLM loop sits in front of it. A tool that returns a three-sentence summary,
five ranked items and evidence ids (section 4, the return contract) isolates
exactly as well as a subagent would, at zero extra model calls and with nothing
to debug in between.

A subagent — its own reasoning loop — earns its cost only when the sub-task
needs several tool calls *and a judgement between them*. "Detect gaps for this
meeting" is one call. "Find out what a stuck decision is missing" is a search,
a read, another search and a comparison, and that is **Research**: the one
subagent, the one that reasons over several sources, and the one that talks to
the outside world.

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

### The return contract — what every tool hands back

```python
class ToolResult(BaseModel):
    ok: bool
    reason: str | None = None        # when ok is False: why, in one line
    summary: str                     # three sentences at most; the orchestrator reads this
    items: list[Finding]             # at most five, ranked by importance
    evidence: list[str]              # utterance ids only, never text
    confidence: float
    truncated: bool                  # True when the cap cut something off
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

Each row is the module owner's own account of what their module can already
answer. An earlier draft asked for tools that do not exist; the owners of B, C
and D corrected it on #261, and their corrections are what this table says.

| Module | Tools | Note |
| --- | --- | --- |
| B | `list_action_items`, `read_classifications`, `read_review` | B's read API, nothing new |
| C | `detect_gaps(checklist=…)`, `topic_graph`, `participation` | one argument added to `detect_gaps` |
| D | `links_for_meeting`, `decision_thread`, `list_decisions` | wraps #185's read routes |
| E | `quality_score`, `trend` | E's aggregate reads |

- **C — `detect_gaps` takes a `checklist: list[str] | None`.** When given, the
  meeting is checked against it; when absent, the built-in domain template
  applies. The team charter (section 7) arrives through this argument, and it
  is what lets gap detection be tuned by a person rather than retrained. This
  is the one signature change the design asks any module for.
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
  trigger     JSONB,   -- why it woke up
  plan        JSONB,   -- what it meant to do
  steps       JSONB,   -- which tools it called, in order
  proposed    JSONB,   -- the plan it submitted for approval (section 8)
  decisions   JSONB,   -- per item: approved | edited | rejected, and the reason
  actions     JSONB,   -- what it actually did
  messages    JSONB,   -- the suspended conversation, for resume (section 8)
  outcome     TEXT,
  latency_ms  INT,
  token_cost  INT,
  created_at  TIMESTAMPTZ
);
```

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
owner (ADR 0009). "Not modified" means no change to a service, a table, a route
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

| Kind | Example | Mechanism |
| --- | --- | --- |
| Time | 09:00 team briefing; 30 minutes before a meeting (see below) | Celery beat |
| State | `next_check_at` due; deadline tomorrow and no signal in three days | 5-minute poll |
| Event | Meeting analysis finished; bot mentioned | Existing events + webhooks |
| Request | "Summarise last week's decisions" | Slash command |

For the first release, **event + state** is enough. Event triggers work today —
the existing events are published and consumed. State triggers depend on the
question in section 13.2: their 5-minute poll needs a beat schedule, and this
repository has none that a module may add.

**The 30-minutes-before trigger does not send a message.** Module D already owns
the pre-meeting brief (`notify.py`, #234), and section 8 rule 2 is that outbound
goes out through the module that owns the content. If the agent has something to
add half an hour before a meeting it adds it to D's brief; it does not post a
second one into the same slot.

## 7. The team charter — judgement the team writes down

Gap detection compares a meeting against a domain template, and the template
is code. That is the wrong place for it twice over: every team gets the same
one, and when it is wrong the only person who can fix it is the module's
owner, by retraining or editing source.

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

1. **As the checklist for gap detection.** Each line under *A meeting must
   settle* becomes an item in `detect_gaps(checklist=…)` (section 4). The
   domain template becomes the default that the charter overrides.
2. **In the orchestrator's system prompt, on every run.** The judgement is
   present each time a plan is made.
3. **As policy for the action model** (section 8). "At most two posts a day"
   is enforced, not suggested.
4. **As the tuning knob.** When gap detection is wrong for a team, the team
   edits a paragraph. No retraining, no issue to another module's owner.

### Three constraints, because a prompt that drives actions is an attack surface

- **A charter can only tighten.** It may add checklist items, lower a post
  limit, demand an owner. It can never grant a permission level, unblock L3,
  or name a destination. Anything in a charter that reads as an instruction to
  a tool is data for judgement, not an instruction — the same rule the
  orchestrator applies to transcript text.
- **"Who must be in the room" is checked at the role level, never the
  person.** "The backend lead was silent on the spec discussion" is a
  per-person speaking-pattern statement about somebody other than the reader,
  which privacy.md section 3 forbids. The check is "no one with the backend
  role spoke on this topic", which is what S20 draws (topic × role, never per
  person), and it is C's per-role participation matrix that answers it.
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

Two module owners asked for the same thing from opposite sides of the
repository, and it turns out to be one rule with five consequences. Numbered,
because a remark gets read once and a rule gets checked.

#### Rule 1 — every outbound transfer, the orchestrator's own LLM call included, goes through `assert_masked` and a stated budget

The LLM call is the one nobody thinks of as outbound. Every run passes tool
results into `llm(messages, …)`, and B's results carry `description`, which
quotes an utterance, and `assignee_label`, which is a person's name.
@kjfcvx12 is right that this is a transfer and not an internal read, and it is
why `L0-ext` exists as its own row.

**It is not blocked, and it is not new.** `privacy.md` section 6 already
governs it and already permits it: *"Anything leaving our infrastructure — LLM
APIs, Slack, Notion, Google Calendar, error tracking, analytics — carries
masked text only, and only what the feature needs."* Two conditions, both
already decided. `packages/integrations/src/autune_integrations/privacy.py` is
the single enforcement point, and its own docstring names "any LLM API"
alongside Slack, Notion and Calendar. So the orchestrator's prompt goes out
through `check_outbound` / `assert_masked` exactly as B's Notion sync and D's
Slack notices do. There is no new mechanism to build and no new decision to
make.

An earlier draft of this document said #92 blocks the layer. It does not.
#92's five questions are consent surviving a departure, label substitution
under PIPA 제36조, the lawful basis for a retained transcript, voice embeddings
under 제23조, and GDPR applicability. **None of them asks whether content may
reach an LLM API.** That dependency was invented here and is removed.

What section 6's *second* condition does impose is a real design constraint,
and it is the one that bites:

| In the prompt | Cap |
| --- | --- |
| Utterance text | **None, by default.** `evidence` is ids (section 4); text is fetched only when a step needs a specific quotation |
| Quoted utterances, when a step needs them | 10, and only from the meeting the step is about |
| Tool results | `summary` plus 5 `items` each, the return contract |
| Whole prompt | `MAX_OUTBOUND_CHARS` (4,000) per `check_outbound` call, and refused past it |

**A transcript never enters a prompt.** A step that needs three action items
sends three action items. This is a budget the loop enforces, not an
aspiration: the return contract already makes it the default, because a tool
hands back a summary and ids rather than rows of text, and `assert_within_size`
already refuses the rest.

One pre-existing limit, stated so nobody reads the above as a promise it does
not make: **a person's name is not in `privacy.md` section 2's masking scope.**
Section 2 covers phone numbers, email addresses, national ID numbers, bank
accounts and card numbers; it does not cover a name or a sentence that
identifies someone by its content, and `find_unmasked` therefore does not catch
either. So `assignee_label` does reach outbound surfaces today. That is true of
B's Notion sync and D's Slack notices as much as of this layer — it is the
system's existing masking scope, not something the agent layer introduces, and
#92 lists it among the things to put to a reviewer.

#### Rule 2 — outbound goes out through the module that owns the content

**The agent reads state and puts it in a briefing. It does not send the DM,
create the Notion page, or re-date the item.** Every module already does its
own outbound exactly once and through its own guard, and a second path is a
duplicate message and a bypassed check at the same time:

| Surface | The module that owns it | What it already does |
| --- | --- | --- |
| Ambiguous-agreement confirmation DM | B | `ext_confirmations` + `send_confirmations`, to the speaker only |
| Notion or Jira page for an item | B | once per (item, system) at confirmation time, recorded in `ext_external_refs` (#294) |
| An item's due date | B | `ext_action_items.due_date` is B's column; "re-date" is not the agent's verb |
| Topic-link notice, decision-drift warning, pre-meeting brief | D | `notify.py`, capped and de-duplicated, implementation in #234 |
| Speaking ratio | E | `feedback.build_speaking_ratio_dm`, DM to the subject only |

So an L2 action is never "the agent sends X". It is "the agent asks the owning
module to send X, and the module's own guard decides". Two consequences worth
naming:

- **Section 6's "30 minutes before a meeting" trigger does not send anything.**
  D's pre-meeting brief already occupies that slot. If the agent has something
  to add there it goes to D's brief, or it waits for the morning briefing.
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

Module C's **risk-scoring step (`gaps`) doesn't produce a value yet — topic
graph and participation already do** (#249 relation extraction is in review;
risk scoring is blocked on #22). An earlier draft said C's pipeline produces
nothing at all, which is not true: `tasks.py` calls `build_topic_graph()` then
`detect_gaps()` then `publish_report()`, writes four tables and publishes
`gap.completed`. Only `GapReport.gaps` comes back empty. This matters beyond
accuracy, because `prd.md` section 5.7's morning briefing names C as a data
source and the two statements have to describe the same module.

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
to the owner as a choice — stood on the two weakest points in the repository:
the ambiguous-agreement classifier catches 17% (#115), and C's risk scoring
does not exist yet. The morning briefing was recommended instead because E's
aggregation and D's read API actually work.

The charter changes that arithmetic. "A performance requirement is a number"
is a checklist line, and checking a transcript against a checklist line is a
prompt over masked utterances, not a topic graph with PageRank on it. It needs
`detect_gaps(checklist=…)` to accept the argument and, until C's risk scoring
lands behind it, a model to answer the question. That is the "T2" track of
section 11 doing real work — on our own inference server, per the constraint
stated there — and it is the first thing in this design that lets the flagship
scenario run against a real meeting in W4.

So the recommendation is now: **both scenarios, in this order.** The morning
briefing first, because it runs on modules that exist and it demonstrates the
loop *not* asking. The charter-driven ambiguous-agreement scenario second,
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

**One task, not three.** Utterance classification has a measured baseline (#149)
and an evaluation set. Gap detection has neither — no risk-scoring implementation
to measure and no labelled gaps. Topic linking is the second task and is
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
  benefit. Section 3.
- **Open-web search in Research, for now.** Research reads uploaded material.
  Section 13.3.
- **The agent sending anything itself.** Outbound goes out through the module
  that owns the content, section 8 rule 2.
- **A classifier behind a third-party API.** Section 11.
- **A charter that can grant anything.** It tightens only. Section 7.
- **A framework.** A hand-written loop plus function calling, on the order of
  200 lines. A graph library makes this harder to debug and harder to explain,
  and explaining it is half the value.
- **Replacing the fixed pipeline.** It stays. The agent is an added path, and
  the demo has a version that does not need it.
- **Autonomy over external systems.** Everything at L2 waits for a person.

### Limits the loop enforces on itself

The context budget, section 9. Past any cap the run stops and hands over to a
person, with the partial trace kept in `agent_runs`.

## 13. Open questions — these block the work

### 13.1 Where does the layer live? — ADR 0009

`packages/agent/` breaks the import-linter contract *Packages do not depend on
modules*. `apps/agent/` breaks invariant 6, *apps is assembly only*. A new
top-level `agent/` breaks neither but adds a layer. Proposed: the third.

### 13.2 How is a periodic trigger registered? — #207, #227

Half of this is solved since the draft was written: #258 closed with #300, so
one Celery app is built once and is current in every process, including the
API's. Reaching Celery from outside a worker is no longer the question.

What is left is the **beat schedule**. There is none, and a module may not edit
`apps/worker` to add one. Every time trigger in section 6 stands on that, and so
does the 5-minute poll the state triggers use. Plan mode's suspend-and-resume
(section 8) needs it too, because the resume is a scheduled wake rather than a
blocked task.

#207 and #227 are the same question asked twice — a module-neutral way to
register a periodic task — and should be decided together.

### 13.3 Which outbound providers, on what terms — not #92, and not a blocker

**The rule is decided; the vendor list is not.** `privacy.md` section 6 says
what may cross the boundary — masked text, only what the feature needs — and
`packages/integrations/privacy.py` enforces it for every destination including
"any LLM API". Section 8 rule 1 applies that to the orchestrator's own LLM call
and states the prompt budget. Nothing about the layer waits on a decision here,
and an earlier draft of this document was wrong to say #92 blocks it: #92 asks
about consent surviving a departure, label substitution under PIPA 제36조, the
lawful basis for a retained transcript, voice embeddings under 제23조, and GDPR
applicability. None of those is about an outbound transfer.

What is actually undecided is narrower and is procurement rather than
architecture: **which providers we send to, and under what agreement.** Two
parts, and only the second holds anything back:

- **The LLM provider.** Open, in the sense that a provider has to be named and
  its data-processing terms recorded before the first real run — the same
  question every third-party integration in `packages/integrations` answers.
  See `../engineering/environments.md` for where a credential and its terms are
  recorded. It does not block design or the first mock-tool milestone.
- **Open-web search.** This one stays out of scope for the release. A search
  query *is* the payload — there is no feature-scoped subset of it to send the
  way there is for a prompt — and a general search engine is not a processor we
  have terms with. So **Research reads uploaded material only**, which is enough
  for the scenario in section 10 and asks nothing of anyone.

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
| Subagents for context isolation | The return contract, section 4 | The point is protecting the parent's context, not dividing labour |
| Plan mode | `submit_work_plan` and the gate, section 8 | Removing write tools raises the quality of the investigation |
| A todo-list tool | `agent_work_items`, section 5 | In a domain with no codebase, the state has to be built |
| An execution trace | `agent_runs`, section 5 | Without observability there is no debugging and no trust |

Related: `module-boundaries.md`, `async-pipeline.md`, `data-model.md`,
`privacy.md`, `../decisions/0009-agent-layer-placement.md`, `../product/prd.md`.
