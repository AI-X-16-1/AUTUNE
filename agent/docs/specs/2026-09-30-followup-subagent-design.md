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
- `kind`: which rule of section 4 fired (#854). `followup_reopened` when a gap
  carried over from the previous meeting, `followup_risky` when high gaps and
  a question left the meeting heavy, `followup_reopened_risky` when both did.
  The pending row keeps `kind` but not the rationale (agent/CLAUDE.md rule 8),
  so the card turns the code into its one line of why. Rows queued before this
  carry `followup_meeting`.

**The suggested date** (`rules.suggest_date`, asked for by the owner on
2026-10-05; revised by #963, proposed). A follow-up meeting checks what M
asked people to do, so it belongs just after that work is due. The date comes
from M's action-item due dates when M has any, and from the team's rhythm
otherwise. **Dates and titles are the rule's values; only the wording of
why is a model's, as a sentence with blanks** (decided 2026-10-08, section 7
"Stage 2"). The date takes no model call: the same inputs give the same date.
The rule also returns what it used (`rules.Why`). A model writes the sentence
that tells the lead why with blanks where the dates and the title go, and
code fills them from `Why` (`explain.py`, below). The model never sees,
chooses or moves a date.

*Inputs.* `today` (the run's date), M's open dated action items as B's
`extraction.meeting_due_dates(M)` reports them (below), and the team's meeting
days from `audio.recent_meetings`' `started_at` for the fallback. An item that
is done, or has no due date, is not an input.

*The rule*, in order; the first step that yields a date wins:

1. **Earliest.** `E` = the next business day after `today`. No suggestion is
   ever earlier than `E`.
2. **Overdue, confirmed only.** If any *confirmed* item's due date is before
   `today`, the date is `E`. Work is already late, so the follow-up should
   not wait for the rest. An *unconfirmed* item whose date is before `today`
   is dropped from every later step instead. Minutes after the meeting, a
   draft's past date is more likely a date B misread (#197) than work already
   late.
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

"Next business day after X" is X plus one day, then moved past Saturdays,
Sundays and Korea's public holidays (#964). The holidays are B's:
`extraction.public_holidays(start, end)` (#985) returns them from the source
B's digests already use -- Google's public holiday calendar, B's table in code
when it has not been read (#838) -- so the two never disagree about a day off.
Follow-up reads today to 45 days on, once, when it proposes. A failed or
missing read means weekends only, as before #964; the proposal never waits on
it. Holidays are dates of public record and say nothing about anybody.

*What the date rests on: `basis`.* The proposal's arguments carry
`basis`, one of three short enums, beside `due_date`:

| `basis` | When | Card |
| --- | --- | --- |
| `confirmed` | Step 2, or step 4 with only confirmed dates left | the date |
| `draft` | Step 4 with at least one unconfirmed date among those left | the date, marked "초안 기준" |
| `cadence` | Step 5 | the date, marked as the team's rhythm |

It is an argument because the pending row stores only `kind`, `arguments` and
`evidence` (agent/CLAUDE.md rule 8), and the card reads the row. Plan mode
accepts a short lowercase enum (#556). At approval `bind_scope` refuses an
argument the write does not declare, so B's `add_followup_item` must take an
optional `basis` before this ships (section 9).

*When B's read is missing or fails.* Step 5. A failed or unregistered
`meeting_due_dates`, a row without `due_dates`, or entries that are not an ISO
date and a boolean all count as "no dates". The proposal still goes out, with
`basis = cadence`. It never waits on B.

*Why the horizon drops items rather than clamps the date.* Clamping step 4's
`P` to 14 days would land on a day no item is due on. It would also still let
a single six-week task pull the date out to the horizon. Dropping keeps the
date tied to a real deadline. The cost is that a meeting whose work is all
long-running gets the rhythm fallback; `basis` (below) tells the card so. If
the dropped items are later most of a team's follow-ups, #22's check will show
it.

*First guesses.* `DUE_DATE_SHARE` (0.8) and `DUE_HORIZON_DAYS` (14) are
first guesses, like section 4's thresholds. They are added to the real-meeting
check of W5 (#22): for each meeting, the suggested date, the date the lead
finally kept on the board, and the share of its items due by then. Two more
measures decide section 7's recompute question: the time from a proposal to
its approval, and the share of M's dated items confirmed at approval.

*B's read.* `meeting_action_items` is not used. Its `body` names each item's
assignee, and it leaves out unconfirmed items. A new read in B's `tools.py`
(#966) returns:

- one row with `due_dates`: a list of `{"date": ISO, "confirmed": bool}`, one
  per open, dated **and confirmed** item, ascending. A list on a single row,
  because `ToolResult` cuts `items` at five and the rule needs every date.
- in `summary`: the counts of open items, undated items and unconfirmed items.
- no title, no assignee, no item text. Evidence is the confirmed items' ids.

**Unconfirmed dates stay in B** (B's owner on #966, 2026-10-07). B's outbound
rule (#246, #261 rule 3) lets nothing of an unconfirmed item leave B but a
count, and a date the card's suggestion is built from would be the first
exception. The after-meeting DM (#953) holds the same line. `confirmed` stays
in each entry, always `true` for now, so the rule and its tests stay as they
are. If B ever hands over draft dates, they count as `draft` with no change
here.

*Titles, for the sentence only.* The date rule reads no title. The reason
sentence names one confirmed item due by the 80% point -- the one due last of
those with a title -- when B's row gives it as `title` on an entry, and counts
the rest ("'API 연동' 등 할 일 4건"). An unconfirmed item's title is never
used (B's rule 3). B hands over a `title` on a confirmed entry
(`meeting_due_dates`, #1038, #1062): the item's description when B's outbound
screen finds no unmasked personal data in it, and no `title` key when it does
(`_shown_title`); without one the sentence counts the items and names none. B
does not mask the title itself -- it passes or withholds it. Cut to 20
characters, it is filled in by code, and Follow-up's own wording call
(`explain.py`) never sends it. In a chat turn the main agent's compose step
does send it: see section 6.

*Why this date: the sentence* (`explain.py`, Stage 2). Each `Suggestion`
carries `why`, the values the rule used: the step (`overdue`, `due_share`,
`cadence`, or `default` when the rhythm is unknown); for step 2 how many
confirmed items are late; for step 4 the due dates counted, how many fall by
the 80% point, that point and the title above, how many were dropped past the
horizon and how many past drafts were dropped; for the rhythm how many meeting
days were read, the latest and the usual gap; whether the day was held to the
earliest business day; and the day before any move off a weekend or holiday,
with the days passed over.

The blanks (`explain.SLOTS`), each filled with its unit:

| Blank | Filled with | Steps |
| --- | --- | --- |
| `{date}` | the suggested day, `10/16(금)` | all; required |
| `{due}` | the 80% point, `10/15` | `due_share`; required |
| `{work}` | `'API 연동' 등 할 일 4건`, `'API 연동' 1건`, or `할 일 4건` without a title | `due_share`; required |
| `{count}` | `4건` | `due_share` |
| `{interval_days}` | `7일` | `cadence`; required |
| `{last_meeting}` | `10/7(수)` | `cadence` |
| `{holiday_shift}` | `공휴일(10/9)을 피해` or `주말을 피해`, by the day passed over first | `due_share`, `cadence`; required when the day moved |
| `{overdue_count}` | `2건` | `overdue` |
| `{default_days}` | `3영업일` | `default` |

When Follow-up proposes, it sends the model `explain.facts(...)`: the step,
`basis`, whether the day moved or was held, the blanks it may and must use
with what each means, and the counts. **Never a date and never a title.** The
model answers a template, for example "{work}을 {due}까지 완료하기로 해서,
결과를 함께 확인할 수 있도록 다음 영업일인 {date}에 후속 회의를 제안했습니다."

A template is used only if it is on one line, one or two sentences, uses
every blank its step requires once and no blank it does not offer, and
outside the blanks has Korean, no date, weekday or day-pinning word
("다음 주", "내일") and no number the counts do not hold. Otherwise -- or with
no model configured, or a call that fails or is refused -- the step's fixed
template (`explain.FALLBACK`) is used, filled the same way. There is one per
step, and one each for a day moved off a holiday and a rhythm day already
past, so the card reads as well without the model. The proposal never waits
on or fails for the model. The sentence is written once, at the proposal, and
the card is not to write it again.

*Not yet shown.* The filled sentence goes into the run's answer and the
proposal's `rationale` today. Neither is stored: the pending row keeps no
rationale (rule 8; #854). Keeping it with the pending row and showing it on the
card is `main/`'s (`models.py`, a migration, `pending.py`, `preview.py`;
김민경), asked on an issue. `explain.Reason` offers the shape that suits rule
8: its `template` holds blanks and no title, so a row could keep the template
alone, and the card fill it from the row's own date and the item's title as B
gives it when the list is read. A deleted item then leaves no title behind.

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
- **What leaves Autune** (decided 2026-10-08; the chat turn added after
  #1032's review, #1069). Follow-up's own outbound request is the reason
  sentence's model call (section 5), and it carries the reason's counts and
  blank names only: the rule's step, `basis`, the blanks it may use and
  counts. No date, no item title, no gap title, no owner, no meeting text:
  dates and the title are filled in by code after the call. It goes through
  `GeminiText`, so `check_outbound` sees it. Until then this read "nothing
  leaves Autune".
  A run asked for in chat makes a second request. The main agent's compose
  step (`main/gemini.py::compose`) sends the run's `summary` and items to
  the model, so the filled reason sentence -- with the item title in it,
  when there is one -- and the cited gaps' titles reach the model there,
  through `check_outbound` (`agent-layer.md` section 3.1, "Where a title can
  reach a model"). A run started by an event composes nothing and makes the
  wording call alone. The only write is still B's, and it runs after the lead
  approves. The item it stores holds B's fixed wording, not gap titles, so no
  topic label is copied into B.
- **The lead is the only reader of the proposal**: approvers with scope
  `followup` in `agent_approvers`. The item it creates starts unconfirmed on the
  board (`add_followup_item`), so it reaches nobody else until someone confirms
  it.
- **Due dates, never owners** (#963). From B, Follow-up reads each open item's
  due date and whether it is confirmed and, for the reason sentence only, a
  confirmed item's title once B hands it over (section 5). It does not read
  assignees. A suggestion built from "who is late" would tell the lead about
  one person through the date, the same reason section 3.1 keeps Follow-up on
  topics. That is why it does not use `meeting_action_items`, whose `body`
  carries the assignee. The overdue step (section 5) reads only that *some*
  item is late, not whose.
- **Stage 2's sentence names no person.** The model's input holds no name,
  title or text, only the rule's counts and the blank names (section 5), so
  there is nothing of a person for it to repeat. Its template is checked for
  blanks it was not offered, dates and numbers it was not given, and one that
  fails is replaced by the step's fixed template. The one title in the filled
  sentence is a confirmed item's description that B's outbound screen
  passed, put there by code. The earlier sketch's member-name check is not
  needed while the wording call sends no title; the chat turn's compose step
  is the main agent's, and its outbound check applies there.

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
- **Unconfirmed due dates at proposal time** (#963). **Decided 2026-10-07: A**,
  with two refinements. A draft's past date is dropped rather than treated as
  overdue (section 5, step 2). The mark travels as `basis` in the arguments.
  B then chose to hand over confirmed dates only; see "What A gives in
  practice" below.
  Follow-up runs on `intelligence.completed`, minutes after B extracted M's
  items. Few of them are confirmed then, so most of the dates section 5 reads
  are B's drafts. Two ways to handle it:

  | | A. Drafts count, the card says so | B. Recompute when items are confirmed |
  | --- | --- | --- |
  | What happens | Step 4 uses confirmed and unconfirmed dates alike. When any date it used is unconfirmed, the approvals card marks the date "초안 기준". | Step 4 uses confirmed dates only. When an item of M is confirmed, edited or rejected, Follow-up computes the date again and the pending proposal is replaced. |
  | For | Works today: one run, one proposal, no new trigger. The lead sees a date while the meeting is fresh. The mark is honest about what the date rests on. | The date rests on dates a person accepted. A draft whose date B misread (#197) never moves the suggestion. |
  | Against | A misread draft date moves the suggestion, and the date does not follow when the item is fixed on the board. B must hand Follow-up the dates of unconfirmed items. That is a date and a flag, not text (#261 rule 3), but it is B's owner's call. The card needs the mark (`main/preview.py`, 김민경). | Needs a new trigger. B's confirmation is no `TRIGGER_EVENTS` event, so this is an event in `packages/contracts` (additive) and a `main/` change (김민경). Each confirmation replaces the pending proposal, so the card's date can change while the lead reads it, and each replacement wakes `notify.py`'s DM again unless it is debounced. Once the lead approves, the item's due date is B's and a person's to move. Recompute must stop there. With nothing confirmed yet, the first proposal has only the rhythm fallback. |

  A needs only B's read and the card's mark. B can follow if #22's check shows
  draft dates moving many suggestions. B's confirmation event would also suit
  other subagents.

  **What A gives in practice** (#966, 2026-10-07). B hands over confirmed
  dates only (section 5, "Unconfirmed dates stay in B"). At
  `intelligence.completed` few items are confirmed, so most proposals made
  right after a meeting carry `basis = cadence`, and `draft` does not occur
  until B changes that. The code and the card's mark (#967) stay as built:
  they cost nothing idle and need no change if it does. A date that follows
  confirmations as they happen is option B, still open. #22's check records
  how often a proposal was `cadence` because nothing was confirmed yet.
- **A fresher date, on the card only** (option C; reshaped 2026-10-07 on
  #972). With confirmed dates only, a proposal made at `intelligence.completed`
  is mostly `cadence`. Recomputing at approval was considered and is **not
  done**: `main/`'s owner holds that the approver runs what the card showed
  ("approve what you saw"), and a date rebuilt inside `pending.approve` could
  differ from it. Instead `preview` computes the date again each time the list
  is read, from the dates confirmed by then, and shows it beside the stored
  one as a hint ("지금 확정 기한으로 계산하면 X"). Approval runs the stored
  arguments unchanged. A lead who wants the new date moves it on the board
  after approving, or rejects the card and gets a fresh proposal. No hook in
  `main/`; the hint sits with #967's mark on the same card.

  *Open:* how `preview` gets the same rule. `main/preview.py` importing
  `autune_agent.subagents.followup.rules` is allowed by import-linter today
  (no contract forbids `main` → a subagent; `main/subagents.py` already
  imports each subagent package), but it ties `main/` to one subagent's
  internals. Asked on #967.

  | | C. Hint on the card | B. On confirmation |
  | --- | --- | --- |
  | New event or contract | None | A confirmation event in `packages/contracts`, a `main/` trigger |
  | Replaced proposals, DMs | None; one DM | One per confirmation, unless debounced |
  | What runs on approval | The stored date; the hint is advice | The latest pending date |
  | Helps when | The lead reads the card after items are confirmed | Always: the pending date follows confirmations |
  | "Approve what you saw" | Holds | Holds |
  | After approval | Both stop: the item's due date is the board's and a person's | — |

  Decided by #22's check (section 5, "First guesses"): the time from proposal
  to approval, and the share of M's dated items confirmed at approval. Mostly
  after confirmation → C's hint is enough. Mostly before → B, or neither.
- **Stage 2: a model-written reason.** **Decided 2026-10-08** by the owner
  (박재경): the date stays the rule's; a model writes only the sentence that
  says why (section 5, "Why this date: the sentence").
  - *What changed from the earlier sketch.* The model gets the rule's values
    as counts and day spans. It gets no dates and no item titles, and its
    sentence names no date: the card already shows the date, and a sentence
    that names one could name a different one. Section 6's "nothing leaves
    Autune" became "the reason's numbers only".
  - *Why a model at all.* The values are enough for fixed wording, and that
    wording is the fallback. A model puts several of them in one readable
    sentence ("기한이 있는 할 일 3개 중 3개가 지난 직후로") where fixed wording
    would need a template per combination.
  - *Why it cannot change the date.* It never sees the date. Its answer is
    only a template, checked against the blanks and values it was given,
    and the date is filled in by code.
  - *Written once.* At the proposal, never when the card is read: the lead
    sees the sentence that was written with the date, and reading the list
    makes no model call.
  - *Revised the same day: dates and a title, by blanks.* A sentence without
    them read thin ("할 일 기한의 대부분이 지난 직후로"). The model now
    writes a template with blanks (`{date}`, `{due}`, `{work}`, ...), and code
    fills them from `Why`. The model still sees no date and no title, so it
    still cannot choose or move the date; the date in the sentence and the
    date on the card are one value. The fixed templates were rewritten to the
    same standard, so a failed call costs little.
  - *Still open.* Where the sentence is kept and how the card shows it
    (`main/`, 김민경); B handing over confirmed items' titles (B, 강민구).
- **Holidays** (#964). Settled: business days skip B's public holidays
  (section 5). A team's own days off (a company holiday) are not known; the
  lead moves the date on the board.

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
  - *All overdue:* every confirmed item before `today` → `E`. *One confirmed
    overdue among later ones:* also `E`. *A draft overdue:* dropped. Alone it
    leaves the rhythm fallback; beside a confirmed date it does not move the
    date. *Due today:* not overdue, so step 4 applies.
  - *Beyond the horizon:* all items after `today + 14` → the rhythm fallback.
    Some beyond, some within → the dropped ones do not move the date. An item
    exactly on `today + 14` is kept.
  - *The 80% point:* n = 1, 2, 5 and 10, with `k = ceil(0.8 × n)` picking
    `d_k`. Duplicate dates count once per item.
  - *Weekends:* `P` on a Friday → Monday; `P` on a Saturday or a Sunday →
    Monday. `today` on a Friday or a Saturday → `E` is Monday.
  - *Never before `E`:* an item due today → `E`, not earlier.
  - *`basis`:* only confirmed dates left → `confirmed`. One draft among them →
    `draft`. A draft dropped by the horizon or as overdue does not make it
    `draft`. No dates → `cadence`.
  - *B's read missing, failed or malformed:* the proposal still goes out, with
    `basis = cadence`, and the meeting list is read only then.
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
| ④ | #963: this revision, `rules.suggest_from_due_dates`, `graph.py` reading B's dates and proposing `basis` | 김민경 (`agent/docs/`), 1 (`subagents/followup/`) |
| — | B's `meeting_due_dates` read, and `add_followup_item` taking an optional `basis` (section 5, #966) | B's owner |
| — | The card's "초안 기준" mark from `basis` (`main/preview.py`, #967) | 김민경 |
| — | Option C's hint: `preview` recomputing the date for the card only (section 7, #972, #967), if chosen | 김민경 |

④ merges after B's `basis` parameter. Before it, every approval fails in
`bind_scope`. `test_the_write_is_one_b_declares_l2_and_takes_the_meeting_a_date_and_a_basis`
pins the parameter and fails until then. B's read can land later: until it
does, every date is `cadence`.

Schedule: ① 10/1 · ② 10/2–10/3 · ③ 10/5 · end to end 10/6–10/8. ② builds
against mock tools for B's two pieces, so it does not wait on #561; end to
end does.
