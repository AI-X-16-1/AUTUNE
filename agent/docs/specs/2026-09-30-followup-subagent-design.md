# Follow-up subagent — design

**Owner:** 박재경 (@PARKJAEKYUNG0525) · **Date:** 2026-09-30 · **Status:** Proposed
· **Gate:** runs end to end on one real meeting by 10/9
(`docs/architecture/agent-layer.md` section 14)

## 1. What it is for

`agent-layer.md` section 3.1: Follow-up watches progress and the gaps nobody
closed, and when another meeting looks needed it **proposes** one, to the team
lead only. The proposal is the approval request itself (section 3.1, first
decision). Nothing reaches anyone else, and nothing is booked, until the lead
approves it.

The demo it has to carry on 10/9: a real recording goes through A–E, the
pipeline's last event wakes Follow-up, it finds a template item the team has
now left open in two meetings running, and a follow-up proposal waits for the
lead. Once approved, it becomes an action item on the board.

### Proposed, for the owner to confirm

| Question | Proposal |
| --- | --- |
| When it runs | On `autune.intelligence.completed`, and on a chat request. No periodic run yet (section 2). |
| What "a follow-up looks needed" means | Rules, no model call (section 4). A template item left open in this meeting **and** in the team's previous analysed meeting, or two or more high-severity open gaps in this meeting together with an unresolved question. |
| What the lead approves | One L2 action per run: B's `extraction.add_action_item`, a "follow-up meeting" item on this meeting (section 5). |
| How the calendar event happens | Not by Follow-up. The approved item gets a due date and an assignee on the board, and B's sync (#441) puts it on that person's own calendar. |
| What it reads about people | Nothing. Open gaps and their topic labels, open questions, and open items. It does not read `silent_share` in this version (section 6). |

### Out of scope

A periodic run (the main agent has no scheduler yet), module D's topic links
(D has no `tools.py`), calendar free/busy reads (the same reason Workload gave
up on them, #435), a model-written proposal, and the approval screen itself
(plan mode, 10/7).

## 2. Deviations from `agent-layer.md`, and why

| Section 3.1 says | This design | Why |
| --- | --- | --- |
| Wakes on state, `@periodic` | Wakes on `autune.intelligence.completed` and chat | `Subagent.triggers` accepts only `TRIGGER_EVENTS` (`main/subagents.py`); there is no periodic trigger yet. A meeting finishing is also the moment the state changes. |
| Reads D's decision threads and topic links | Recurrence comes from C alone | A topic belongs to one meeting, but a template item key (`risk`, `ownership`, …) is the same across meetings. "Open in two meetings running" needs no cross-meeting topic link. D's links can refine it once D ships `tools.py`. |
| Reads Calendar `free_busy` | No calendar read | A team account sees one Workspace only, and a person's own grant serves only their own work (#435, as recorded in Workload's `__init__.py`). |
| Proposes a calendar event | Proposes a board item | The only write Follow-up may name is a module's declared action. `add_action_item` exists, is L2, and reaches a calendar through #441 once it has a due date and an assignee. |
| Reads a topic's `silent_share` | Does not | The rule does not need it, and it is the one participation figure in the inputs (section 6). |

The doc change to `agent-layer.md` is PR ③ (section 9).

Like Research, Follow-up wakes on `intelligence.completed` because it reads B's
and C's results, which do not exist yet at `transcript.ready`. When E
re-aggregates and publishes again, the trigger's redelivery guard skips the
second event (#509). Section 7 covers a gap dismissed in between.

## 3. Flow

Scope: the run's `team_id`, and meeting M. On the trigger, M is the event's
meeting. On a chat request the scope has no meeting, and Follow-up takes the
team's most recent analysed meeting (`audio.recent_meetings`).

| # | Node | Does | Calls |
| --- | --- | --- | --- |
| 1 | `read` | `gap.open_gaps(M)`, `gap.recurring_open_gaps(M)`, `extraction.unresolved_questions(M)` | 3 tools |
| 2 | `decide` | Applies the rule in section 4. No trigger → finish, `ok=True`, no proposal. | — |
| 3 | `propose` | Composes the item text from gap titles (section 5), then proposes one L2 `extraction.add_action_item`. | — |

At most 3 tool calls on the trigger path and 5 on a chat run. A failed read
ends the run with `ok=False` and no proposal. A meeting with no open gaps is not
a failure.

No model call. Like Workload, the decision is a rule the team can read and
test, and nothing leaves Autune.

## 4. The rule

Follow-up proposes when either holds for meeting M:

1. **Carried over.** At least one template item key is open in M and was also
   open in the team's previous analysed meeting. *Open* means an undismissed
   gap on that key.
2. **Left heavy.** M has `FOLLOWUP_MIN_HIGH_GAPS` (2) or more open
   high-severity gaps, and B reports at least one unresolved question.

The thresholds live in the subagent's config, so they can move without a code
change. Both are first guesses to be checked on the real meetings of W5 (#22),
the same set that measures C's precision.

## 5. New pieces

### ① C's tools: `modules/gap/src/autune_gap/tools.py`

The first `tools.py` in module C. It returns plain dicts and imports nothing
from the agent layer (agent/CLAUDE.md rule 2).

- `gap.open_gaps(session, meeting_id)`: M's undismissed gaps, highest risk
  first, at most five. Each has its title, severity, `template_item_key`, the
  suggested question and the related topic labels. Evidence is the gap ids.
- `gap.recurring_open_gaps(session, meeting_id)`: the template item keys open
  in M and in the team's previous analysed meeting. The previous meeting is the
  team's latest earlier meeting that C has analysed, meaning it has a topic
  graph (the same test C's report and rescore use). Each key comes with both
  meetings' gap ids as evidence. Built in #546.
- `RUN_SCOPE = ("team_id",)`, as in B and E. A meeting from another team reads
  as missing.
- `PERSONAL_ONLY_TOOLS = []`. C has no per-person figure to offer.

Neither tool returns participation, per person or per role.

### ② The subgraph: `agent/src/autune_agent/subagents/followup/`

- `rules.py`: the section 4 rule, as pure functions over tool results.
- `graph.py`: the three nodes.
- `__init__.py`: exports `SUBAGENT`, with
  - `tools`: the three reads plus `audio.recent_meetings`
  - `triggers = (INTELLIGENCE_COMPLETED,)`

### The proposal

One `ProposedAction` naming `extraction.add_action_item`, with these arguments:

- `meeting_id`: M.
- `description`: `후속 회의: <item title>, <item title> …`. At most three
  titles, highest risk first. The titles come from C's gap titles, which are a
  template's item name plus a masked topic label. No utterance text is used.
- `assignee_id`, `due_date`: left empty. The lead fills them on the board after
  approving. The due date is what puts the meeting on a calendar (#441).

`agent_runs` keeps the proposal's arguments and the gap ids, and no other
text, as settled on #509.

## 6. Privacy

- **Topics, never people or roles** (section 3.1, second decision). No tool in
  the allow-list returns a per-person or per-role figure, and `Subagent` refuses
  a personal-only tool by name.
- **`silent_share` is not read in this version.** It is a topic-level
  aggregate and section 3.1 allows it. But in a two- or three-person meeting a
  share of one-half says a lot about one person, and the rule does not need it.
  If a later rule wants it, that is a decision recorded here first.
- **Nothing leaves Autune.** There is no model call and no outbound request. The
  only write is B's, and it runs after the lead approves.
- **The lead is the only reader of the proposal**: approvers with scope
  `followup` in `agent_approvers`. The item it creates starts unconfirmed on the
  board (`add_action_item`), so it reaches nobody else until someone confirms
  it.

## 7. Open questions

- **A gap dismissed between the proposal and the approval.** The proposal was
  built from gaps the team may have since dismissed (#509 review, inline 2).
  Re-reading the evidence at approval time is plan mode's question (10/7). Until
  then, the lead sees the gap ids and can reject the proposal.
- **Repeated proposals.** A key that stays open meeting after meeting would
  produce a proposal each time. Proposed: do not propose again while an earlier
  Follow-up item for the same keys is still open on the board. That needs a
  read B does not have yet (items by description prefix, or a source marker),
  so it waits on B.
- **Periodic runs.** "Deadline passed, nothing moved" is a state that no event
  signals. It comes with the main agent's scheduler.

## 8. Testing

- **Rules** (unit): carried-over only; heavy only; both; neither. A dismissed
  gap does not count as open. The previous meeting is the team's, never another
  team's.
- **Subgraph** (unit, `mock_tool`): no trigger means no proposal. At most 3
  tool calls on the trigger path. Exactly one L2 proposal, naming
  `extraction.add_action_item`, whose `description` holds only gap titles. A
  failed read ends `ok=False` with no proposal. A chat run picks the most recent
  analysed meeting.
- **C's tools** (PostgreSQL): another team's meeting reads as missing;
  dismissed gaps are left out; `recurring_open_gaps` finds the previous
  analysed meeting and skips one C never analysed; no participation field in
  either result.
- **End to end** (10/6–10/8): two real recordings for one team, the second
  leaving a template item open again. The proposal appears in
  `agent_runs.proposed`. Until plan mode lands there is no approval endpoint, so
  approval is a direct `add_action_item` call, as Research does it.

## 9. Delivery

| PR | Content | Approvals |
| --- | --- | --- |
| ⓪ | This spec, on its own, so the proposals in section 1 can be settled first | 김민경 (`agent/docs/`) |
| ① | C's `tools.py`: `open_gaps`, `recurring_open_gaps` | 1 (module C) |
| ② | Follow-up subgraph and `SUBAGENT` | 1 (`subagents/followup/`) |
| ③ | `agent-layer.md` section 3.1 row and section 6: the deviations in section 2 | 5 (`docs/`) |

Schedule: ① 10/1 · ② 10/2–10/3 · ③ 10/5 · end to end 10/6–10/8.
