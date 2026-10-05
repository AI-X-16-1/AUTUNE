# Follow-up subagent — design

**Owner:** 박재경 (@PARKJAEKYUNG0525) · **Date:** 2026-09-30 · **Status:** Built (#547, #563)
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
| When it runs | On `autune.intelligence.completed`, and on a chat request. No periodic run (section 2). |
| What "a follow-up looks needed" means | Rules, no model call (section 4). A template item left open in this meeting **and** in the team's previous analysed meeting, or two or more high-severity open gaps in this meeting together with an unresolved question. |
| What the lead approves | One L2 action per run: B adds a "follow-up meeting" item to this meeting, marked as Follow-up's (section 5). Its arguments are ids and an enum only, as plan mode requires (#556). |
| How the calendar event happens | Not by Follow-up. Four steps: the lead approves; someone gives the item an assignee and a due date on the board; someone confirms it; B's sync (#441, `sync_after_confirmation`) puts it on the assignee's own calendar. Nothing reaches a calendar before the item is confirmed. |
| What it reads about people | Nothing. Open gaps and their topic labels, open questions, and open items. It does not read `silent_share` in this version (section 6). |

### Out of scope

A periodic run (section 7), module D's topic links
(D has no `tools.py`), calendar free/busy reads (the same reason Workload gave
up on them, #435), a model-written proposal, and the approval screen itself
(plan mode, 10/7).

## 2. Deviations from `agent-layer.md`, and why

| Section 3.1 says | This design | Why |
| --- | --- | --- |
| Wakes on state, `@periodic` | Wakes on `autune.intelligence.completed` and chat | When this was written, `Subagent.triggers` accepted only `TRIGGER_EVENTS`. `Periodic` exists since #637, but the rule reads one meeting's state, which changes only when a meeting finishes or C republishes after a dismissal, and both arrive as this event. |
| Reads D's decision threads and topic links | Recurrence comes from C alone | A topic belongs to one meeting, but a template item key (`risk`, `ownership`, …) is the same across meetings. "Open in two meetings running" needs no cross-meeting topic link. D's links can refine it once D ships `tools.py`. |
| Reads Calendar `free_busy` | No calendar read | A team account sees one Workspace only, and a person's own grant serves only their own work (#435, as recorded in Workload's `__init__.py`). |
| Proposes a calendar event | Proposes a board item | The only write Follow-up may name is a module's declared action. B's item reaches a calendar through #441 once it is confirmed with an assignee and a due date. |
| Reads a topic's `silent_share` | Does not | The rule does not need it, and it is the one participation figure in the inputs (section 6). |

The doc change to `agent-layer.md` is PR ③ (section 9).

Like Research, Follow-up wakes on `intelligence.completed` because it reads B's
and C's results, which do not exist yet at `transcript.ready`. When E
re-aggregates and publishes again, the republished event is a new task, so
Follow-up runs again and its new proposal supersedes the one still pending
(`pending.queue_l2`). Only a redelivered message is skipped (#509). Section 7
covers a gap dismissed in between.

## 3. Flow

Scope: the run's `team_id`, and meeting M. On the trigger, M is the event's
meeting. On a chat request asked from a meeting's screen, M is the scope's
meeting. Only when the scope has no meeting does Follow-up take the team's most
recent analysed meeting (`audio.recent_meetings`).

| # | Node | Does | Calls |
| --- | --- | --- | --- |
| 1 | `read` | `gap.open_gaps(M)`, `gap.recurring_open_gaps(M)`, `extraction.unresolved_questions(M)`, and B's read of an open Follow-up item (section 7) | 4 tools |
| 2 | `decide` | Applies the rule in section 4. No trigger, or a Follow-up item still open → finish, `ok=True`, no proposal. | — |
| 3 | `propose` | Proposes one L2 action: B's Follow-up item on M, with the gap ids as evidence (section 5). | — |

At most 4 tool calls on the trigger path and 5 on a chat run with no meeting in
its scope. A failed read
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
- `gap.gaps_by_id(session, meeting_id, gap_ids)`: of the given ids, M's gaps
  nobody has dismissed, in `open_gaps`' item shape. The approvals card reads a
  proposal's evidence through it, so a cited gap ranked below the top five
  does not read as closed (#644).
- `RUN_SCOPE = ("team_id",)`, as in B and E. A meeting from another team reads
  as missing.
- `PERSONAL_ONLY_TOOLS = []`. C has no per-person figure to offer.

No tool returns participation, per person or per role.

### ② The subgraph: `agent/src/autune_agent/subagents/followup/`

- `rules.py`: the section 4 rule, as pure functions over tool results.
- `graph.py`: the three nodes.
- `__init__.py`: exports `SUBAGENT`, with
  - `tools`: the three reads plus `audio.recent_meetings`
  - `triggers = (INTELLIGENCE_COMPLETED,)`

### The proposal

**No text in the arguments.** Plan mode (#556) queues an L2 proposal only when
its arguments are ids, ISO dates, booleans and short lowercase enums
(`main/pending.py`, `arguments_ok`); text a proposal needs lives in the owning
store and is pointed at by id. An earlier draft of this section passed
`description="후속 회의: <gap titles>"` to `extraction.add_action_item`, which
the queue refuses (`ARGUMENT_REFUSED`), so the lead would never have seen it.

One `ProposedAction`, with:

- `tool`: `extraction.add_followup_item`, B's L2 action for this (#561). It
  takes `team_id` and `meeting_id`, writes the fixed description "후속 회의
  잡기" itself, records the item as Follow-up's (`origin="followup"`) rather
  than a person's, and starts it unconfirmed. Not `add_action_item`: that is
  B's chat-draft write and L1 since #576, so it would run without the lead.
- `arguments`: `{"due_date": D}` on a triggered run, whose scope binds the
  meeting when the action runs; `{"meeting_id": M, "due_date": D}` on a chat
  run that picked M itself. No assignee: the lead picks one on the board after
  approving. D is a suggested date (below), stored as the item's due date, so
  the lead sees it on the card and moves it on the board; the due date is what
  puts the meeting on a calendar (#441).

**The suggested date** (`rules.suggest_date`, asked for by the owner on
2026-10-05). The team's usual gap between meetings -- the median of the gaps
between its recent meetings' start days, one to 14 days -- after its latest
meeting, never before the next business day, a weekend moved to Monday. With
fewer than two meeting days, or an unreadable list, it is three business days
from today; holidays are not known. It reads `audio.recent_meetings`'
`started_at` and nothing else: no calendar and nobody's availability, so
section 6 holds. Free time in the lead's own calendar is the card's to show,
from the lead's own connection when the lead opens it (#435's rule that a
personal grant serves only its owner).
- `evidence`: the gap ids the rule fired on, highest risk first.

**What the lead sees.** Plan mode renders a preview from read tools when the
list is read and stores nothing for display (its spec, section 6). This needs a
preview row for B's Follow-up write that shows the evidence's gap titles through
`gap.gaps_by_id(M, evidence)` (#562, #644); without it the lead sees only the
kind and the ids. A title is a template's item name plus a masked topic label,
so no utterance text is shown.

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
  only write is B's, and it runs after the lead approves. The item it stores
  holds B's fixed wording, not gap titles, so no topic label is copied into B.
- **The lead is the only reader of the proposal**: approvers with scope
  `followup` in `agent_approvers`. The item it creates starts unconfirmed on the
  board (`add_followup_item`), so it reaches nobody else until someone confirms
  it.

## 7. Open questions

- **A gap dismissed between the proposal and the approval.** The proposal was
  built from gaps the team may have since dismissed (#509 review, inline 2).
  Answered by the preview: the card re-reads the evidence each time the list is
  read, so a gap dismissed since drops out, and when none is left the card says
  so (#562, #626).
- **Repeated proposals.** A key that stays open meeting after meeting would
  produce a proposal each time. Decided: Follow-up does not propose while a
  Follow-up item of the team's is still open on the board (unconfirmed, to do or
  in progress). Per team rather than per key, because a key list is not an
  argument plan mode accepts, and one open follow-up meeting is enough to carry
  them. The read is B's `open_followup_item(team_id)` and keys on
  `origin="followup"`, not on the description, which a person may reword
  (#561). `add_followup_item` also refuses while one is open, so a second
  approved proposal makes no second item. A pending proposal for the same meeting
  is already superseded by plan mode (#556).
- **B's edit-cost figure.** Settled by #561: the item is recorded with
  `origin="followup"`, so E's edit-cost figure does not count it as an item a
  person added.
- **What "carried over" cannot see.** An item is the same item only under the
  same template (#546): a template switch between two meetings carries nothing
  over. And the previous meeting is the latest one C analysed even when it
  raised no gap, so a meeting in between that settled everything breaks the run.
  Both are intended. D's topic links could bridge a template switch once D ships
  `tools.py`.
- **Periodic runs.** "Deadline passed, nothing moved" is a state that no event
  signals. The main agent can wake a subagent on a timer since #637
  (`Periodic`); Follow-up declares none until it has a rule for that state,
  recorded here first.

## 8. Testing

- **Rules** (unit): carried-over only; heavy only; both; neither. A dismissed
  gap does not count as open. The previous meeting is the team's, never another
  team's.
- **Subgraph** (unit, `mock_tool`): no trigger means no proposal; an open
  Follow-up item means no proposal. At most 4 tool calls on the trigger path.
  Exactly one L2 proposal naming B's Follow-up write, whose arguments pass
  `arguments_ok` and whose evidence is gap ids. A failed read ends `ok=False`
  with no proposal. A chat run uses the scope's meeting when it has one, and
  the most recent analysed meeting otherwise.
- **C's tools** (PostgreSQL): another team's meeting reads as missing;
  dismissed gaps are left out; `recurring_open_gaps` finds the previous
  analysed meeting and skips one C never analysed; `gaps_by_id` finds a gap
  `open_gaps` cuts and leaves out dismissed ids and other meetings' ids; no
  participation field in any result.
- **End to end** (10/6–10/8): two real recordings for one team, the second
  leaving a template item open again. The proposal is queued with scope
  `followup`, shows the gap titles on the approvals page, and once approved the
  item appears unconfirmed on the board.

## 9. Delivery

| PR | Content | Approvals |
| --- | --- | --- |
| ⓪ | This spec, on its own, so the proposals in section 1 can be settled first | 김민경 (`agent/docs/`) |
| ① | C's `tools.py`: `open_gaps`, `recurring_open_gaps` | 1 (module C) |
| ② | Follow-up subgraph and `SUBAGENT` | 1 (`subagents/followup/`) |
| — | B's Follow-up write and its open-item read (#561) | B's owner |
| — | The approvals-page preview for that write (#562) | 김민경 |
| — | C's by-id read for that preview (#644) | 1 (module C) |
| ③ | `agent-layer.md` section 3.1 row and section 6: the deviations in section 2 | 5 (`docs/`) |

Schedule: ① 10/1 · ② 10/2–10/3 · ③ 10/5 · end to end 10/6–10/8. ② builds
against mock tools for B's two pieces, so it does not wait on #561; end to
end does.
