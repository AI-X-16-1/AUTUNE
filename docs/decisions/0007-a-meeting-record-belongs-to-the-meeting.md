# 0007. A meeting record belongs to the meeting, not to its participants

**Status:** Proposed
**Date:** 2026-09-09
**Deciders:** needs the whole team — it narrows rule 4 of ADR 0003, and
`../architecture/privacy.md` section 8 says a rule that blocks you is raised,
not worked around. This ADR is that raise.

## Context

`../architecture/privacy.md` section 4 currently says:

> When a user leaves a team, their utterances and everything derived from them
> are deleted.

That sentence and ADR 0006 cannot both hold. ADR 0006 made source utterances
load-bearing: every action item shows the utterances it came from, and that is
how a user checks an item without replaying the meeting. Delete a departed
member's utterances and every item they committed to loses its evidence. Delete
*everything derived from them* and the items and decisions go with it.

The damage is not confined to module B. Module D keys a decision lineage on B's
`dec_` ids and walks a chain through `ctx_decision_versions`; removing one
decision breaks the chain at a link rather than at its end, and a lineage with a
hole reads as complete. `ExtractionResult.action_items[].assignee` resolves to a
`user_id`, so an open commitment held by a departed member has no representable
state at all — neither closed nor assigned.

And there is the plain fact that *"the team decided to ship on Friday"* does not
stop being the team's decision when one participant leaves. A record that erases
itself as people move on is not a record.

**The schema already contains the answer.** Section 4 requires that every
module-owned table be reachable from a `meeting_id` **or** a `user_id`. Two
ownership paths have been there since `packages/core` landed, and neither was
given a meaning. Deciding what each one means decides this question.

The consent point is also already in the right place. Section 5 notifies
participants when recording starts and excludes a non-consenting participant's
speech. So consent attaches to *"my speech is part of this meeting's record"*,
and it is given at the meeting. Leaving the team later does not un-hold the
meeting.

*Nobody on this team is a lawyer. The statute references below are the shape of
the problem, not a legal opinion, and this ADR should not ship without review.*

## Decision

**The meeting owns its record. A participant holds access to it, not ownership
of it.** Leaving a team is an access change, not a data change.

The path by which a row is reachable is its owner:

| Reachable from | Owner | When a member leaves |
| --- | --- | --- |
| `meeting_id` — `utterances`, `ext_*`, `gap_*`, `ctx_*`, `intel_*` | the meeting | **unchanged** |
| `user_id` — `aud_speaker_embeddings`, account, sessions, tokens | the person | **deleted** |

`participants` is the boundary row: it belongs to a meeting and references a
user. The row stays and its `user_id` clears.

**The schema already does this.** `participants.user_id` is nullable with
`ON DELETE SET NULL`, so the boundary behaves as described today, before this ADR
is adopted. What is decided here is the rule, not a change to the tables — read
this section as naming existing behaviour rather than requesting a migration.

That is the whole mechanism. There is no de-identification migration and no
per-module anonymisation path — the earlier draft of this ADR required one in
every module, and this version requires none. A record nobody has to operate on
is a record nothing can corrupt.

Four things the chat-room analogy does not carry over. Each is decided here
rather than left to the analogy.

**1. Retention is what actually bounds this.** "Deleted when everyone leaves"
almost never fires: a team rarely empties, and one dormant account would hold a
record indefinitely. The 90-day default in section 4 stays and is the real bound.
Ownership decides *whose* the data is; retention decides *how long* we keep it.
Both are needed and they answer different questions.

**2. Display names are kept, and replaceable on request.** A departed member's
name stays on their utterances, so the record reads as it did on the day. On a
PIPA 제36조 request, that person's label is replaced with `참석자 N` — scoped to
one meeting, and deliberately not stable across meetings, since a stable label
would let anyone rebuild the person by joining their meetings together.

Statements survive that replacement with their meaning intact, because a decision
does not depend on who voiced it. *"금요일에 배포하기로 했다"* needs an owner, and
the owner is a separate field. This is why keeping names by default is affordable:
the fallback costs readability, not correctness.

**3. Speaking ratio is not shared with the room.** Everything else in a meeting
is visible to its members. This one is not — section 3, ADR 0003 rule 3, and
`IntelligenceSnapshot`, which says of speaking ratios *"they are not in this
contract, and never will be"*. Adopting a shared-room model means naming its one
exception, not quietly widening it.

**4. Joining a team does not grant its past.** A member added today does not see
meetings held before they joined unless a meeting is explicitly shared with them.
Access derives from the participant list, not from team membership. Otherwise
adding a person to a team would be an act of disclosure by everyone already in
it, performed by whoever clicked invite.

Two rules follow for module B specifically.

**An open commitment is reassigned, never orphaned.** When an assignee leaves,
the item stays and its assignee clears. `ActionStatus` gains no value — the item
keeps `todo` or `in_progress` and carries `needs_reassignment`, so it surfaces at
the top of the action board (S17) instead of sitting invisibly unowned. A closed
item keeps `done` and needs nothing.

**Missing attribution is shown, not hidden.** After a label replacement the quote
stays and is attributed to `참석자 N`. If an utterance is genuinely deleted, the
item keeps its statement and says the source is gone; it does not render a blank
and it does not reconstruct text from an embedding. `../modules/context.md`
already requires exactly this of dangling topic links, so the pattern exists.

## Alternatives considered

**Keep section 4 as written — delete everything.** Rejected: it contradicts ADR
0006, breaks D's lineage mid-chain, and destroys the organisation's record of its
own decisions to protect data whose subject consented to it being part of that
record.

**De-identify each departed member's rows.** This ADR's first draft. Rejected:
it reached the same retention outcome by a harder road — a per-module
anonymisation path, surgery on shared rows, and a consent argument resting on
가명처리 versus 익명처리 rather than on who the record belongs to. PIPA 제28조의2
permits processing 가명정보 without consent only 「통계작성, 과학적 연구,
공익적 기록보존」, and running a product feature is none of those, so that road
also needed an argument it could not make. The ownership rule needs no such
argument.

**Keep everything, including the voice embedding.** Rejected: an embedding is
생체인식정보 and the strongest re-identifier we hold, and no part of keeping a
record needs it. It is on the `user_id` path and it goes.

**Delete when the last member leaves, and rely on that alone.** Rejected — see
decision 1. It is a condition that almost never occurs.

**Give a new member the full history by default.** Rejected — see decision 4.

## Consequences

**Easier.** No de-identification migration in any module. One question —
*which path reaches this row?* — replaces per-module judgement, and it is
testable in a way the previous draft was not: a table reachable only from
`user_id` must hold no meeting content, and a table reachable from `meeting_id`
must not require a live user. Both are assertions about a schema, which is
exactly the kind of test that stays true.

Module D's lineage spans staff turnover, which is its whole premise — a lineage
that resets when someone leaves is worthless at the one-year scale it is for.

**Harder.** Access control moves from team membership to the participant list.
That is a larger change than it sounds: every read path needs it, and it is the
part of this ADR most likely to be underestimated. `participants.user_id` becomes
nullable and every consumer handles that. `needs_reassignment` is new UI in
`features/actions/`.

**Contract impact, and this is why it is urgent.** `packages/contracts` freezes
after W1 and only additions are permitted afterwards:

- `ActionItem.assignee` must be able to represent unassigned. It resolves to a
  `user_id` today.
- `ActionItem` needs `needs_reassignment`.
- `ContextLinks.decision_lineage[].key_stakeholders_absent` names people and
  takes the same treatment as a display label.
- Consumers of `ActionItem.source_utterances` and `Decision` must tolerate a
  replaced label and, in the deletion case, a missing utterance. That is a
  documented expectation rather than a field.
- `TranscriptReady.utterances[].speaker_id` already permits null for a
  diarised-but-unidentified speaker, and `contracts.md` already says *"Handle
  this case — it is common."* Label replacement reuses that path rather than
  adding one.

**Accepted cost.** A retained transcript is not anonymous. Utterance text is
PII-masked before the first write (section 2), which handles phone numbers and
주민등록번호; it does not handle content that identifies a person by what it
describes — *"제가 지난주에 부산 지사에서 확인했는데"* is not anonymous with the
name removed. We claim the identity link is cleared and the content is masked,
not that the record is anonymous, and the honest place for that sentence is the
privacy policy.

**Revisit if** legal review rejects keeping display names by default. The
fallback is `참석자 N` from the moment of leaving rather than on request, and the
model supports it without restructuring — decision 2 changes, nothing else does.

## On acceptance

`../architecture/privacy.md` section 4 changes:

- Replace *"When a user leaves a team, their utterances and everything derived
  from them are deleted"* with the ownership rule.
- Keep the 90-day retention and *"A user can delete their own data at any
  time"* — both are unchanged and decision 1 depends on the first.
- *"Deletion is real. No soft deletes, no tombstones holding content"* stays and
  now also governs a replaced label: the old label is gone, not hidden.
- Add to the review checklist: which path reaches this table, and does it hold
  the other kind of data?
- Add: access to a meeting derives from its participant list, not from team
  membership.

ADR 0003 rule 4 gains a pointer to this ADR. It is narrowed, not superseded —
the other four rules are untouched.
