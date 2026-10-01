# Plan mode — the approval queue for L2 proposals

**Owner:** 김민경 (@mkkim68), main agent · **Date:** 2026-09-30 · **Due:** 10/7
(promised to 이승환 on #449) · **Gate:** Research runs end to end on one real
meeting by 10/9

## 1. What it is for

Subagents already return `ProposedAction`s. L1 runs at the end of a run (#509).
L2 — a write that moves a person — has nowhere to go: it is recorded as a
proposal and then nothing happens. So no research document can be approved,
no report is published, no reassignment is made, and the research card (#527)
stays empty.

Plan mode is the piece that holds an L2 proposal until a person with the right
scope approves or rejects it, and on approval runs it.

### Decided with the owner on 2026-09-30

| Question | Decision |
| --- | --- |
| How much of `agent-layer.md` section 8's plan mode to build | An approval queue only. Every subagent is a deterministic graph that already returns proposals; there is no LLM planning loop to add. |
| Where a pending proposal's content lives | Its arguments are ids and short scalars only, and are stored in the queue. Text lives in the owning module's store first and is pointed at by id — as E's report draft and Research's document already are. |
| E re-publishing `intelligence.completed` after a late source (박재경 on #509) | A new run. The trigger's redelivery guard keys on the Celery task id; an older `pending` proposal from the same subagent for the same meeting becomes `superseded`. |
| Rejection reason | A fixed choice, never free text. |
| Report's approver | A new scope, `report`. |
| Who sets approvers | Not decided (section 13.5). Seeded with SQL for the demo; the statement is documented. |
| Where approvers act | One team-wide "승인 대기" page. |

### Out of scope

An LLM planning loop, editing a proposal before approving it, Slack approval
buttons, an approver settings screen, and "repeated approval proposes
demotion to L1" (section 8).

## 2. Deviation from `agent-layer.md` section 8, and why

Section 8 describes plan mode as a loop in which the model plans with read-only
tools, submits a plan with `submit_work_plan`, and resumes from stored
`messages` after approval. The subagents that shipped (#493, #508, #525) do
their planning inside their own graphs and hand back proposals, so the loop
would re-plan what is already planned. It would also need `messages` — a copy
of tool output — kept for hours, which the storage rule settled on #509 does
not allow. Section 8 is updated to say this (PR C).

## 3. Flow

1. **Queue.** At the end of `run_and_record`, after L1 has run, each L2
   proposal -- marked L2, or marked L1 for an action its module declared L2 --
   is checked:
   - Its arguments must all be ids (`[a-z]+_[A-Za-z0-9]+`), ISO dates, booleans,
     or short enum strings (`[a-z_]{1,32}`). Anything else is refused: the
     proposal is not queued, and the run's `actions` record says so with a
     reason this layer wrote. The same holds for a `kind` outside
     `[a-z_]{1,64}`, a `tool` outside `[a-z_.]{1,128}`, or a subagent name
     outside `[a-z_]{0,32}`.
   - A proposal that passes is inserted as `pending`, with its approver scope
     (section 4).
   - Any earlier `pending` proposal with the same subagent and the same
     `meeting_id` becomes `superseded`. A proposal about no meeting supersedes
     nothing.
2. **List.** `GET /api/agent/pending` returns the pending proposals
   the caller may approve, each with a preview (section 6), and, under the
   same rules, every `approved` row whose `result_ok` is unset -- an approval
   interrupted after its claim -- marked `needs_check` so it is not hidden.
3. **Approve.** `POST /api/agent/pending/{id}/approve` re-checks the caller,
   rebuilds the run's scope (`RunScope(team_id, meeting_id)` from the row), and
   runs the action through the same path L1 uses (`bind_scope`, the action's
   declared level is ignored here because a person approved it). The row
   becomes `approved` with the result, or `failed` with a reason this layer
   wrote. A `PrivacyViolationError` is re-raised (the rule from #509).
4. **Reject.** `POST /api/agent/pending/{id}/reject` with one of
   `wrong_evidence`, `not_now`, `handled_elsewhere`, `other`.

Approve and reject change a row only while it is `pending`; the update is a
conditional `UPDATE … WHERE status = 'pending'`, so two approvers clicking at
once run the action once. The loser gets 409.

### The trigger's redelivery guard

`on_event` today skips a subagent that has a finished run for the same event and
meeting, which also skips E's legitimate re-publish. The tasks in
`autune_agent.tasks` become `bind=True` and pass `self.request.id`; it is
recorded in `agent_runs.trigger` as `task_id`, and the guard skips only a run
with the same `task_id`. A redelivery carries the same id; a re-publish is a new
task with a new id.

## 4. Storage

```
agent_pending_actions
  id            text PK              pa_…
  team_id       → teams      ON DELETE CASCADE
  meeting_id    → meetings   ON DELETE CASCADE, nullable
  run_id        → agent_runs ON DELETE SET NULL
  subagent      text                 research | workload | followup | report | …
  tool          text                 e.g. agent.share_research_document
  kind          text
  arguments     jsonb                ids and short scalars only (section 3)
  evidence      jsonb                ids only
  scope         text                 the approver scope required
  status        pending | approved | rejected | superseded | failed
  reject_reason text                 one of four codes, or null
  result_ok     boolean, nullable
  result_reason text                 a reason this layer wrote, or null
  decided_by    → users      ON DELETE SET NULL
  decided_at, created_at
```

No title, body or free-text reason is stored. A row is deleted with its meeting.
A proposal about no meeting (Workload's reassignments) holds ids only, so there
is nothing in it a deletion could miss.

**Scope mapping:** `research` → `research`, `workload` → `workload`,
`followup` → `followup`, `report` → `report`; any other subagent → `any`.
`agent_approvers.scope`'s check constraint gains `report`. An approver with
scope `any` approves everything in the team.

**Demo approvers:**
`INSERT INTO agent_approvers (team_id, user_id, scope) VALUES ('<team>', '<user>', 'any');`

## 5. API

| Route | Who | Returns |
| --- | --- | --- |
| `GET /api/agent/pending` (optional `team_id`) | any signed-in user | pending rows in every team where the caller is an approver with the row's scope (or only `team_id`'s when given), newest first, each with `preview`; an empty list for a non-approver |
| `POST /api/agent/pending/{id}/approve` | an approver with the row's scope or `any` | the row after execution |
| `POST /api/agent/pending/{id}/reject` `{reason}` | same | the row |

With `team_id`, a non-member gets 403; without it there is nothing to refuse — the list is the caller's own approver rows. The approvals page calls it without `team_id`, so the agent feature needs no other feature's endpoint for a team list. An id of another team, or one that does not exist, is
404, and the response never echoes it. A row that is no longer `pending` is
409.

## 6. Preview

Nothing is stored for display. The preview is read when the list is read, through
the modules' existing read tools and the layer's own tables, under the row's
scope:

| Tool | Preview |
| --- | --- |
| `agent.share_research_document` | the document's body from `agent_research_documents` |
| `extraction.reassign_action_item` | `extraction.action_item_status` for the item (B hides an unconfirmed item's text), and the new assignee's display name |
| `intelligence.publish_meeting_report` | "리포트 초안 — 회의 대시보드에서 보기" with the meeting link; E has no read tool for a draft yet, and an issue asks 이승환 for one |
| anything else | the kind, the subagent and the ids |

If the source is gone the preview says "원본이 더 이상 없습니다" and the row can
only be rejected. When the source is deleted, the preview disappears with it; no
copy survives.

## 7. Screen

A new feature folder, `apps/web/src/features/agent/` (owner: main agent), and a
route `apps/web/src/app/(app)/approvals/`. The page lists the caller's pending
proposals as cards: which subagent proposed what, the preview, a link to the
meeting when there is one, **승인**, and **거절** with the four reasons. After a
decision the card shows the result and leaves the list. The header gains a
"승인 대기" link. `CODEOWNERS` gains `features/agent/`.

## 8. Testing

- **Queue** (unit): only L2 is queued; a free-text argument is refused with a
  reason; an earlier `pending` proposal of the same subagent and meeting is
  `superseded`; decided rows are untouched; a proposal about no meeting
  supersedes nothing.
- **Approve and reject** (unit): runs under the stored scope; a scope
  mismatch is refused; a failure records `failed` and this layer's reason;
  `PrivacyViolationError` propagates; a second decision is 409.
- **API** (unit): only approvers with the scope see and decide; another team's
  id is 404; a non-member is 403.
- **Preview** (unit): each of the three previews; a missing source.
- **Deletion** (PostgreSQL): deleting a meeting deletes its pending rows.
- **Trigger guard**: the same task id is skipped; a new task id runs and
  supersedes.
- **End to end** (10/6–10/8): a real meeting's Research document is queued,
  approved on the page, and appears on the research card.

## 9. Delivery

| PR | Content | Approvals |
| --- | --- | --- |
| A | table and migration (`report` scope), queueing and superseding, approve/reject/execute, previews, API, task-id guard | 1 (`agent/` only) |
| B | `features/agent`, the approvals route, the header link, `CODEOWNERS` | 5 |
| C | `agent-layer.md` section 8, the L2 argument rule, the demo approver SQL; one line in `agent/CLAUDE.md` | 5 |

Schedule: A 10/1–10/2 · B and C 10/3 · review 10/4–10/6 · due 10/7 · end to end
10/6–10/8. The issue to 이승환 for E's draft read tool goes out with PR A.
