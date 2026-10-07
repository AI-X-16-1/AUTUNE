# Follow-up subagent — design

**Owner:** 박재경 (@PARKJAEKYUNG0525) · **Date:** 2026-09-30 · **Status:** Built (#547, #563)
· **Gate:** runs end to end on one real meeting by 10/9
(`docs/architecture/agent-layer.md` section 14)
· **Revision 2026-10-07:** the suggested date follows the meeting's action-item
due dates (#963), proposed — section 5 "The suggested date", sections 6–8

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
its scope. `propose` adds the reads its suggested date needs (section 5): B's
due dates and, when it falls back, the team's meeting days. A run that
proposes nothing spends neither. A failed read
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

- `evidence`: the gap ids the rule fired on, highest risk first.

**The suggested date** (`rules.suggest_date`, asked for by the owner on
2026-10-05; revised by #963, proposed). A follow-up meeting checks what M
asked people to do, so it belongs just after that work is due. The date comes
from M's action-item due dates when M has any, and from the team's rhythm
otherwise. No model call: the same inputs give the same date, and the lead can
be told why.

*Inputs.* `today` (the run's date), M's open dated action items as B's
`extraction.meeting_due_dates(M)` reports them (below), and the team's meeting
days from `audio.recent_meetings`' `started_at` for the fallback. An item that
is done, or has no due date, is not an input.

*The rule*, in order; the first step that yields a date wins:

1. **Earliest.** `E` = the next business day after `today`. No suggestion is
   ever earlier than `E`.
2. **Overdue.** If any input item's due date is before `today`, the date is
   `E`. Work is already late, so the follow-up should not wait for the rest.
3. **Horizon.** Drop every input item due after `today + DUE_HORIZON_DAYS`
   (14). They are long-running work. A meeting about them is a later
   meeting, not this follow-up. If nothing is left, go to step 5.
4. **Most of the work.** Sort the remaining due dates ascending, `d1 ≤ … ≤ dn`.
   Take `P = d_k` with `k = ceil(DUE_DATE_SHARE × n)` (`DUE_DATE_SHARE` = 0.8).
   The date is the next business day after `P`, but never before `E`.
5. **Fallback: the team's rhythm.** The rule as built in #852: the team's
   usual gap between meetings -- the median of the gaps between its recent
   meetings' start days, one to 14 days -- after its latest meeting, never
   before `E`. With fewer than two meeting days, or an unreadable list, it is
   three business days from today.

"Next business day after X" is X plus one day, then moved past Saturday and
Sunday. Holidays are not known (#964); the lead moves the date on the board.

*Why the horizon drops items rather than clamps the date.* Clamping step 4's
`P` to 14 days would land on a day no item is due on. It would also still let
a single six-week task pull the date out to the horizon. Dropping keeps the
date tied to a real deadline. The cost is that a meeting whose work is all
long-running gets the rhythm fallback; the card says which rule was used. If
the dropped items are later most of a team's follow-ups, #22's check will show
it.

*First guesses.* `DUE_DATE_SHARE` (0.8) and `DUE_HORIZON_DAYS` (14) are
first guesses, like section 4's thresholds. They are added to the real-meeting
check of W5 (#22): for each meeting, the suggested date, the date the lead
finally kept on the board, and the share of its items due by then.

*B's read.* `meeting_action_items` is not used. Its `body` names each item's
assignee, and it leaves out unconfirmed items. A new read in B's `tools.py`
(an issue to B's owner) returns:

- one row with `due_dates`: a list of `{"date": ISO, "confirmed": bool}`, one
  per open dated item, ascending. A list on a single row, because `ToolResult`
  cuts `items` at five and the rule needs every date.
- in `summary`: the counts of open items, undated items and unconfirmed items.
- no title, no assignee, no item text, confirmed or not. Evidence is the item
  ids.

Titles are not read in this stage. Stage 2 (section 7) may read the titles of
confirmed items only, under B's outbound rule (#261 rule 3).

The suggestion reads no calendar and nobody's availability, so section 6
holds. Free time in the lead's own calendar is the card's to show, from the
lead's own connection when the lead opens it (#435's rule that a personal
grant serves only its owner).

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
- **Due dates, never owners** (#963). From B, Follow-up reads each open item's
  due date and whether it is confirmed, and nothing else. It does not read
  assignees. A suggestion built from "who is late" would tell the lead about
  one person through the date, the same reason section 3.1 keeps Follow-up on
  topics. That is why it does not use `meeting_action_items`, whose `body`
  carries the assignee. The overdue step (section 5) reads only that *some*
  item is late, not whose.
- **Stage 2's sentence names no person.** If a model later writes the card's
  reason (section 7), its input is the date, the rule step that produced it and
  confirmed item titles. It gets no assignee, speaker or participant name. Its
  output is checked for the team's member names before it is shown, and a
  sentence that names one is replaced by the rule's own wording.

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
- **Unconfirmed due dates at proposal time** (#963, for the owner to decide).
  Follow-up runs on `intelligence.completed`, minutes after B extracted M's
  items. Few of them are confirmed then, so most of the dates section 5 reads
  are B's drafts. Two ways to handle it:

  | | A. Drafts count, the card says so | B. Recompute when items are confirmed |
  | --- | --- | --- |
  | What happens | Step 4 uses confirmed and unconfirmed dates alike. When any date it used is unconfirmed, the approvals card marks the date "초안 기준". | Step 4 uses confirmed dates only. When an item of M is confirmed, edited or rejected, Follow-up computes the date again and the pending proposal is replaced. |
  | For | Works today: one run, one proposal, no new trigger. The lead sees a date while the meeting is fresh. The mark is honest about what the date rests on. | The date rests on dates a person accepted. A draft whose date B misread (#197) never moves the suggestion. |
  | Against | A misread draft date moves the suggestion, and the date does not follow when the item is fixed on the board. B must hand Follow-up the dates of unconfirmed items. That is a date and a flag, not text (#261 rule 3), but it is B's owner's call. The card needs the mark (`main/preview.py`, 김민경). | Needs a new trigger. B's confirmation is no `TRIGGER_EVENTS` event, so this is an event in `packages/contracts` (additive) and a `main/` change (김민경). Each confirmation replaces the pending proposal, so the card's date can change while the lead reads it, and each replacement wakes `notify.py`'s DM again unless it is debounced. Once the lead approves, the item's due date is B's and a person's to move. Recompute must stop there. With nothing confirmed yet, the first proposal has only the rhythm fallback. |

  Recommendation: A for stage 1. It needs only B's read and the card's mark.
  B can follow if #22's check shows draft dates moving many suggestions. With
  B the confirmation event would also suit other subagents.
- **Stage 2: a model-written reason** (after #963). The card could say why in a
  sentence, for example that two items are due on Thursday and Friday and the
  results can be compared the following Monday. A model writes only that
  sentence from the rule's output (the date, the step used, the dates it
  rested on, confirmed item titles). It never chooses or moves the date, and
  it names no person (section 6). It goes through `packages/integrations` like
  every model call and falls back to the rule's own wording when the call
  fails or is refused. This changes section 6's "nothing leaves Autune" and is
  decided here before it is built.
- **Holidays** (#964). Business days skip weekends only. A Korean holiday list
  is a separate change to every "next business day" in section 5.

## 8. Testing

- **Rules** (unit): carried-over only; heavy only; both; neither. A dismissed
  gap does not count as open. The previous meeting is the team's, never another
  team's.
- **The suggested date** (unit, `rules.py`, #963). Every case passes `today`
  in and reads no clock. Fixed `today` = Wednesday 2026-10-07, so `E` =
  Thursday 10-08.
  - *Same inputs, same date:* the same items in two orders, and the same call
    twice, give one date.
  - *No due dates:* no items, or only undated or done ones → the rhythm
    fallback (#852's cases still pass unchanged).
  - *All overdue:* every item before `today` → `E`. *One overdue among later
    ones:* also `E`. *Due today:* not overdue, so step 4 applies.
  - *Beyond the horizon:* all items after `today + 14` → the rhythm fallback.
    Some beyond, some within → the dropped ones do not move the date. An item
    exactly on `today + 14` is kept.
  - *The 80% point:* n = 1, 2, 5 and 10, with `k = ceil(0.8 × n)` picking
    `d_k`. Duplicate dates count once per item.
  - *Weekends:* `P` on a Friday → Monday; `P` on a Saturday or a Sunday →
    Monday. `today` on a Friday or a Saturday → `E` is Monday.
  - *Never before `E`:* every item due today or tomorrow → `E`, not earlier.
  - *Confirmation:* under option A (section 7) a date resting on an
    unconfirmed item is flagged as such; under option B unconfirmed dates are
    ignored. Only the chosen option's case is kept.
- **Privacy of the read** (unit, `mock_tool`): the subgraph gets no assignee.
  A tool result carrying a name field fails the test. No date or reason
  string Follow-up builds contains a member's name.
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
| ④ | #963 spec: section 5's date rule, sections 6–8 (this revision) | 김민경 (`agent/docs/`) |
| — | B's `meeting_due_dates` read (section 5) | B's owner |
| ⑤ | #963 code: `rules.suggest_date` from due dates, `graph.py` reads B's dates, `__init__.py` allow-list | 1 (`subagents/followup/`) |
| — | Option A's "초안 기준" mark on the approvals card, or option B's confirmation event (section 7) | 김민경 |

Schedule: ① 10/1 · ② 10/2–10/3 · ③ 10/5 · end to end 10/6–10/8. ② builds
against mock tools for B's two pieces, so it does not wait on #561; end to
end does.
