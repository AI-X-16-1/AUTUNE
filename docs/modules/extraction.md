# Module B. Structured Extraction

| | |
| --- | --- |
| **Package** | `autune_extraction` |
| **Owner** | 강민구 |
| **Backend** | `modules/extraction/` |
| **Frontend** | `apps/web/src/features/actions/` |
| **Table prefix** | `ext_` |
| **API prefix** | `/api/extraction` |

## Responsibility

Turn utterances into trackable structure: classify what kind of statement each
utterance is, build action-item cards from commitments, verify ambiguous
agreement, and sync the result to Notion and Jira.

## Non-goals

- Finding what was *not* discussed — that is C. B works from what was said.
- Linking to past meetings — that is D.
- Team-level aggregation and scoring — that is E.

## Inputs

| Source | Contract |
| --- | --- |
| A | `TranscriptReady` via `autune.transcript.ready` |
| `packages/core` | `meetings`, `participants`, `utterances` (read-only) |

## Outputs

| Destination | Contract | Event |
| --- | --- | --- |
| D, E | `ExtractionResult` | `autune.extraction.completed` |
| Notion, Jira | Issue creation via `packages/integrations` | — |
| Slack | Action-item card thread, confirmation DMs | — |

## Pipeline

1. **Classify** — a fine-tuned DeBERTa classifier (`kakaobank/kf-deberta-base`,
   see "AI stack") over each utterance, in spoken order: `commitment`,
   `decision`, `open_question`, `concern`, `ambiguous`, or **`none`** — most of a
   meeting is none of them (#149).
   `none` never leaves this module: an utterance the model calls none is simply
   absent from `ExtractionResult.classifications`, and has no row in
   `ext_classifications`. Inference runs before any database transaction opens;
   it is minutes of CPU per meeting.
2. **Resolve references** — LLM resolves pronouns and elided subjects ("그거",
   "저희가") against surrounding utterances.
3. **Slot fill** — one draft action item per commitment. The assignee is the
   speaker: their `user_id` when identified, otherwise only the transcript's
   label. The due date is the first Korean date phrase in the utterance
   ("다음 주 화요일", "월말", "9/20"), resolved against the day the meeting was
   held in Korea, and the phrase itself is kept in `due_text`. With no meeting
   start time, a relative phrase keeps its words and gets no date — the upload
   time is not the meeting time. Anything unsettled is left empty and the item
   stays in *needs confirmation*.
   **Noun-ended wording (`noun_form.tidy`).** What is stored as an item's
   description and a decision's statement is the sentence tidied into the form a
   record uses: "그럼 제가 다음 주 화요일까지 볼게요" becomes "다음 주 화요일까지 볼
   예정", "A안으로 진행합시다" becomes "A안으로 진행함". A fixed list of endings
   and a few fillers, not a model; a sentence with a negation or a question in it,
   or an ending the list does not know, is kept as it was said. The original
   utterances stay in `ext_*_sources` and are shown beneath the line — the
   drawer's "근거 발화" for an item, the row's "원본 발화" for a decision — so
   the person confirming reads one against the other. Only the tidied line, as
   the person confirmed or reworded it, leaves Autune: an item goes out only
   after it leaves *needs confirmation*, a decision only once confirmed, and
   never the original utterance. With `resolver_impl=llm` the description is also
   a **summary**: the model reads the commitment, the lines around it and up to
   eight lines from elsewhere in the meeting that share its subject (found by
   word overlap, `pipeline/related.py`), writes one sentence, and says which
   lines it used. Those lines are stored (`ext_action_item_related`) and shown
   beneath the summary as "요약에 쓴 발화", above the quotation, so a person can
   check the sentence against what it was made from and correct it — the
   description is editable like any other. The candidates are only offered: a
   line nobody cites is neither stored nor shown, and a citation the model
   invents (a number that is no line, the commitment itself) is dropped.
   With `classifier_impl=llm` the sentence comes earlier and from the classifier
   (2026-10-06): its answer carries, beside the label, one line for each
   commitment and decision, in the request that read the line. A line that
   passes the checks (`llm.usable_summary`: one line; every `[사람N]` put back
   as the name it stood for; no number or name that is not in the line or the
   three said before it) is the description, and the resolver is asked only
   about a commitment that has none. The classifier does not say what it
   drew on, so that is read off the line: of the three lines with text said
   just before -- the ones the summary was checked against, except that the
   check also stops at the start of its request -- those that hold a word the
   summary has and the commitment itself does not (`related.drawn_on`) are
   stored in `ext_action_item_related` and shown as "요약에 쓴 발화". A summary
   that added nothing from them cites none, and the drawer shows the quotation
   alone.
   A decision is written up the same way (`ext_decision_related`, "요약에 쓴
   발화" on S15) -- but only when its settling turn does not say what was decided:
   short, or pointing at something said before ("그렇게 하죠"). Asked about every
   decision, the model rewrote all of them and cited a line for about a quarter;
   the rest it only put into "~하기로 했습니다", which `noun_form.tidy` does without
   a model. That limit is the resolver's. With `classifier_impl=llm` **every
   decision the classifier wrote a line for shows that line**, whether or not
   its settling turn already said what was decided, and cites the lines it
   took a word from, found the same way (`ext_decision_related`): the owner
   asked for each
   action item and decision as one line (2026-10-06), and the line came with
   the label at no further request. A decision with no such line goes the
   resolver's way as before.

   **What module D is sent is not what the screen shows.** `ext_decisions.statement`
   is the line a person sees and that leaves for Notion -- noun-ended, or the
   write-up. `original_statement` is the sentence as assembled from the utterances
   (the turn that settles it, plus owner and deadline), and that is the
   `Decision.statement` in the contract, unless a person reworded the decision, in
   which case it is their wording. D embeds statements and compares them against a
   similarity threshold tuned on that shape (`context.config`), so nothing made for
   the screen may reach it; D reads the utterances themselves through
   `source_utterance_ids` as before. The contract is unchanged. A sentence that names nothing ("다음 주
   화요일까지 볼 예정") is read with up to three lines said just before it, shown
   apart from the sources as "앞선 발화 (맥락)"; nothing fills the missing object
   into the line itself unless a model writes the line -- the reference resolver
   (`resolver_impl`, off by default) or, with `classifier_impl=llm`, the
   classifier's own one-line summary. The due date is still read from the original
   words, which carry the verb ending it depends on.
4. **NLI verification** — check whether an apparent agreement entails an actual
   commitment. Weak assent ("한번 볼게요") is labeled `ambiguous`. Before this
   step a fixed rule takes the label off an `ambiguous` turn that is nothing but
   an acknowledgement ("네 알겠습니다."): it has no content to ask the speaker
   about, so it is not verified, recorded or asked. The same words labeled
   `commitment` -- an acceptance of a request -- are left alone.
5. **Build decision entities** — group the utterances classified as decisions
   into `Decision` records with a `dec_` id and the statement as settled. One
   decision often spans several utterances. **Module D depends on this**: it is
   what a decision lineage is keyed on, and a `Classification` alone is not
   enough. See `../architecture/contracts.md`, "The B → D boundary".
6. **Confirm** — every ambiguous agreement is recorded in `ext_confirmations`
   first, then the speaker gets a Slack DM. Until the DM goes out the row is
   *not asked* and `AmbiguousAgreement.confirmation_sent` is false. Every five
   minutes `ask_confirmations` asks each one recorded within the 72-hour window
   whose speaker is identified, consented and is on the meeting's team now,
   through the team's Slack bot to the account that person linked (#255,
   #478), and to nobody else. A speaker who has left the team is not asked,
   and a button pressed on a DM they were sent before leaving is not recorded
   -- the web refuses them the same answer. A team
   without Slack, or a speaker who has not linked, is looked at again on the
   next run until the window closes.
   **Where the speaker answers** (decided with the user, 2026-10-01; #585). The
   DM quotes their line and links to the meeting's 액션 tab, which lists the
   reader's own open questions (`GET /confirmations?meeting_id=`) with the
   three answers (`POST /confirmations/{utterance_id}`: commitment, decision,
   not a commitment). With `AUTUNE_SLACK_BUTTONS` on — a deployment Slack can
   reach at `/api/slack/events` (#585) — the DM also carries the three answers
   as buttons; without it, only the link, since a button nothing receives does
   nothing. Either way the answer takes the same path. A speaker who never linked Slack can still answer
   there — answering puts the question, so its clock starts then. Nobody but
   the speaker sees or answers it.
   **A DM whose line is corrected afterwards** (#586). Each DM keeps where it
   landed (`dm_channel`, `dm_ts`) and a digest of what it quoted; a run that
   finds the line hashing differently queues `update_confirmation_dm`, which
   rebuilds the DM from the stored line and replaces it in place
   (`SlackClient.update_message`, `chat.update`) under the same outbound check
   as a send. A DM sent before places were kept cannot be corrected.
   **What the answer does.** *Commitment* makes one draft item for that
   utterance, slot-filled like any commitment (the speaker is the assignee, the
   first date phrase the due date, the utterance's own text — tidied into the
   noun form, as in step 3 — the description, until the summary below replaces
   it),
   in *needs confirmation* with confidence 1.0 — the speaker's answer is the
   certainty, and the team still accepts the item before it leaves for Notion
   or a calendar. Any other answer makes no item; a later answer replaces an
   earlier one, so changing *commitment* to *not a commitment* takes the draft
   back unless a person has moved or edited it since. A rerun of the meeting
   keeps the draft (it is derived from `ext_confirmations` again) and never
   makes a second one. `ext_classifications` is not rewritten: it records what
   the model said and `resolved_kind` what the speaker said, and the two stay
   comparable.
   **The summary comes after the answer** (decided with the user, 2026-10-01).
   The DM quotes only the speaker's line. A *commitment* answer sends
   `summarise_confirmed_draft`, which writes a summary from the lines around the
   agreement the way a commitment's is written (step 3) and puts it on the
   draft as its description, the lines it cited beside it — shown on the board,
   the speaker's line beneath it, never in Slack. Only a confirmed agreement is
   summarised, so an unanswered one costs no model call. A draft a person has
   touched, or an answer changed in the meantime, is left as it is. A rerun
   summarises the confirmed ones again so their drafts keep a summary.
7. **Sync** — when a person confirms an action item (moves it out of
   `needs_confirmation`), create one page for it in the team's Notion database
   and store the URL in `ext_external_refs` (#30). One page per item: a later
   edit updates it (#342). If someone deletes that page in Notion, the next edit
   makes a new one; if someone archives it, it is left archived (#403). The
   board does not say so yet: later edits to that item stop reaching Notion
   and only the log records it. S18's integration row is where an "archived in
   Notion" state belongs once it exists. A create that timed out on our side
   may still have made the page: when the item's last Notion copy failed as
   `unreachable`, the next create first asks the database for a live page
   with exactly the item's title made since shortly before that failure, and
   keeps it if there is exactly one (review of #754). A team without Notion
   connected is skipped. Not
   part of the extraction run: nothing the model drafted is confirmed yet (#246).
   A decision goes the same way when a person confirms it (or adds it), to the
   team's decision database, in the wording they confirmed
   (`ext_decision_refs`). A decision that stops being confirmed after that —
   put back to pending, rejected, deleted, or dropped by a rerun — does not
   keep its page: the
   decision database has no status column, so a page left in place would go
   on reading as a confirmed decision (#246). The page is retitled to
   "확정이 취소된 결정" and then moved to Notion's trash, so the trash does
   not keep the statement; the row stays without a page, and confirming again
   makes a new one. A page a person had already archived is left to them.
   A retire that fails is tried again at the decision's next change, and for
   a decision that is gone only when the Notion backfill runs — on
   connecting Notion or by hand, not on a timer (#669).
   A deleted action item's page is retitled "삭제된 액션아이템" the same way
   before it goes to the trash (#768), at deletion and when
   `drain_external_cleanup` retries it.
   A confirmed item with a due date also goes on its **assignee's own Google
   Calendar** as an all-day event with no attendees, through that person's grant
   in `user_integrations` (#435, #444); team work is not copied into anyone's
   calendar. Every ten minutes `pull_calendar_changes` reads back Autune's own
   tagged events on each connected calendar, and a date the person moved there
   becomes the due date through the board's edit path (`ext_calendar_events`,
   `ext_calendar_polls`). An event on the calendar of somebody who has since
   left the meeting's team is taken off by a sweep every ten minutes
   (`take_back_departed_calendar_events`): leaving a team starts no sync of
   its own, and the event's title is the item's text. The sweep deletes and
   never writes an event -- an item that has a new assignee gets theirs from
   its own sync.
   A confirmed item is also one issue in the team's Jira project (#82, #458),
   and every ten minutes `pull_jira_changes` reads back the status people moved
   their issues to: an issue dragged to Done makes its item done, through the
   same edit path. `ext_external_refs.synced_category` records what Autune last
   left the issue in, so a board edit that has not reached Jira yet is never
   undone; when both moved, the board keeps its status and Jira's is taken as
   the new baseline (nothing is sent back from the read-back). Each issue is
   read in its own transaction, least recently read first (`pulled_at`); an
   issue Jira refuses to show is skipped. A ref with no baseline yet (made
   before the read-back, or its issue never took the board's status) gets
   Jira's category recorded as one, and the board is left alone. An item
   moved back to 확인 필요 keeps its issue: the issue follows the item's
   text and stays in the status the team has it in (#657).
8. **Publish** — emit `ExtractionResult`.

Classification runs before reference resolution, which is worth stating because
the opposite reads as more natural: resolve the pronouns, then work on clean
text. Two things decide it.

The class is marked at the sentence ending in Korean, and the referent does not
carry it — "이걸 확정하도록 **하겠습니다**" is a commitment whether or not anything
knows what 이걸 points at. Slot filling is the step that genuinely cannot proceed
unresolved, and it comes after resolution either way.

Resolution is an LLM call. Running it first means one per utterance; running it
after classification means one per utterance in the classes that still need it —
roughly an eleventh as many on a corpus of 398,748 meeting utterances. That also
points the same way as `privacy.md`, which asks for the smallest window that
resolves a reference rather than the whole meeting.

Neither argument is an accuracy measurement — comparing the two orders needs a
labelled set and two trained classifiers. If the evaluation harness later shows
resolution-first classifies better, moving the step is the cheap direction to go;
building on an LLM call per utterance and cutting it back later is not. Keep the
step positionable. `modules/extraction/scripts/ko_reference_overlap.py` measures
the overlap the question turns on.

**When a person deletes their own speech** (#587): `tasks.forget_deleted_speech`
(`@on_speech_deleted("extraction")`) runs before the utterances go. Unconfirmed
drafts the model or the chat made from them are deleted — except one that was
confirmed once and moved back, which still has a page or an issue outside and
is treated as a confirmed item, so the words do not stay there with no row left
to find them by (#657); a confirmed item whose
description is the line itself reads "삭제된 발화에서 만든 항목" and its
`due_text` is cleared; a decision loses `original_statement`, and a model
statement that is the settling line tidied reads the same placeholder; a model
summary or a person's text stays -- for a decision that is the classifier's
one line or the resolver's write-up, marked when it is stored
(`ext_decisions.statement_resolved`) and not by whether it cites a line. Changes to a row that has copies outside — a confirmed one,
or an item moved back to 확인 필요 that kept them (`service.copies_follow`) — are
queued to Notion, Jira (summary and description) and the calendar. Nothing is
republished: what C, D and E already
received in `ExtractionResult` stays with them until they act on the same
signal. Ids and counts only in the log.

**When a line is corrected after the fact** (#586). A PII report (S30, #584)
masks stored lines again and republishes `TranscriptReady` without naming them.
Every item and decision keeps `source_digest`, a sha256 of the masked text it was
drawn from (set when it is made, and recorded as a baseline by the first run that
finds none). Each run compares it after its rebuild — also in a meeting a person
has edited, where the rebuild keeps every item: a description that is the line
itself reads the corrected line, tidied; a model summary is replaced the same way
and flagged `needs_recheck`; a person's own text (a typed item, an edited
description, a typed or reworded decision) is only flagged, because B cannot tell
which of their words were the private ones; `due_text` is read again from the
new line. Changes are queued to Notion, Jira and the calendar under the same
rule as for a deleted speech (`service.copies_follow`). The flag
shows on the card and on the decision ("출처 발화가 정정됨 · 확인 필요") and is
cleared by the person's next edit or review. A line that was *deleted* is not a
corrected one: `forget_speech` drops the digest of every row it leaves behind,
because the lines that remain hash differently from a digest taken over all of
them, and the next run records a baseline instead of rewriting or flagging. The
confirmation DM's quotation is #586's second part.

## Tables

| Table | Purpose |
| --- | --- |
| `ext_classifications` | Per-utterance kind, confidence, model version, NLI result. Kinds only — no row for `none` |
| `ext_action_items` | Assignee, description, due date, status, origin |
| `ext_action_item_sources` | Which utterances an item came from |
| `ext_decision_related` | The other lines of the meeting a decision's summary was written from, as the model said it used them; shown beneath the summary, never read by D |
| `ext_action_item_related` | The other lines of the meeting the item's summary was written from, as the model said it used them (`LlmResolver`); shown beneath the summary, never read by D or E |
| `ext_edit_events` | One row per correction. Counts only — no person on it |
| `ext_external_refs` | The Notion page an action item became, one per item and system |
| `ext_decision_refs` | The Notion page a confirmed decision became, one per decision and system |
| `ext_calendar_events` | The event an item's due date became on its assignee's own calendar, and the date last synced |
| `ext_calendar_polls` | When each person's calendar was last read back |
| `ext_calendar_cleanup` | Due-date events still to take off a person's calendar after their meeting expired; queued by the meeting hook, removed by `drain_calendar_cleanup` with the owner's grant (#588). No meeting key; `user_id` cascades |
| `ext_external_cleanup` | A deleted item's Notion page or Jira issue the deleting request could not trash or close: team, system, page id or issue key, and Jira's site. Ids only. Retried by `drain_external_cleanup` every ten minutes; a team not connected now is kept, a transient failure counted up to five, a refusal or another site's key dropped (#692). `team_id` cascades |
| `ext_notion_targets` | The page and three databases a team's Notion sync writes to, one row per team (#428) |
| `ext_confirmations` | Every ambiguous agreement, the DM once sent, and the response |
| `ext_sync_failures` | That an item's latest copy to Notion, Jira or a calendar failed: the system, one of four kinds (`privacy`, `reconnect`, `unreachable`, `rejected`) and the time (#680). Never the outside service's message, never what was being sent. Removed by the next copy that goes through; goes with the item |
| `ext_sync_retries` | When "다시 시도" was last pressed for an item; a second press within 30 seconds is refused (429) rather than running Notion, Jira and the calendar again. One time per item; goes with the item |
| `ext_due_reminders` | That an item's assignee was sent a due-date reminder of one kind (`due_soon`, `overdue`) for one due date — the "once" — or that the outbound check refused it, reported once and not tried again. No text, no person; goes with the item |
| `ext_due_reminder_optouts` | A person who turned their own due-date reminders off (연동 screen › 내 연결), and Monday's DM of their own open items with them (#792): one switch for both. On unless a row says off; the person and when, nothing else. Goes with the account |
| `ext_decisions` | Decision entities, their statements and source utterances. `origin` is `model` or `user`; a rerun rebuilds only the model's |
| `ext_decision_sources` | Which utterances a decision was settled in, in order |
| `ext_decision_reviews` | A person's verdict on each proposed decision (pending, confirmed, rejected) and an optional rewording, keyed by `dec_` id so a rerun over the same sources keeps it (#246). No reviewer column |
| `ext_extraction_attempts` | One row per meeting whose extraction failed, whose stored result could not be published, or that a person asked to extract again: failures in a row, the class of the last error (never its message), when, when the team's Slack channel was told, and the request the worker takes. Deleted with the meeting |
| `ext_extraction_runs` | One row per extracted meeting: a digest of the consenting utterances the last run read, and when (#518) |
| `ext_meeting_notes` | The team's memo on a meeting's summary tab (S15 요약, #421). Free text a member typed; no author column; a blank memo is no row |
| `ext_meeting_summaries` | A meeting's summary written by a cloud model, only with `AUTUNE_EXTRACTION_SUMMARY_IMPL=llm` (#421 v2): an overview, points one per line, the model, and a digest of the lines it was written from. One per meeting, deleted with it. A summary whose lines have changed is not shown and is deleted by the next run; deleted speech deletes it at once |
| `ext_forgotten_utterances` | The ids of utterances a person deleted, from B's speech hook until module A has removed the rows, so no summary is written from them in between (#782). An id and a time, nothing said; each row goes with its utterance |
| `ext_weekly_digests` | That a person was sent Monday's DM of their own open items for one week through one team's Slack (#792). The primary key is the "once"; the message is not kept |
| `ext_daily_digests` | That a person was sent the morning DM for one day through one team's Slack. The primary key is the "once", and the latest row's time is where the next DM's "since the last one" starts; the message is not kept. Goes with the person and with the team |
| `ext_notification_pauses` | One range of days a person set for themselves on which the morning DM and Monday's DM are not sent. Dates only; read and written by that person alone, shown to nobody else, deleted once the range has ended. Goes with the account |
| `ext_public_holidays` | The public holidays no digest goes on: one row a day, as Google's public calendar of Korea's holidays listed it at the last read, with that read's time (`days_off.py`). Replaced whole on every read; not used once the newest read is two weeks old. Dates of public record -- nothing about a person, a team or a meeting |
| `ext_projects` | A team's projects as its members name them: a name, other names people say for it, and optionally its own Jira project key (#786). Typed by a member, not derived from speech; goes with the team. `ext_decisions` and `ext_action_items` point at one through `project_id` |
| `ext_project_sends` | Where a project's minutes for one meeting were sent, per tool (#787): the Notion page id, the Slack message as `channel:ts`, or the Jira issue key, so sending again updates that copy, and a digest of the minutes it last received, so a refresh leaves an unchanged copy alone. Addresses and a hash, no text; goes with the meeting and with the project |
| `ext_project_send_cleanup` | Copies of project minutes still to take out of a team's tool after their meeting or project was deleted, and half a Notion page that could not be taken back (#787): team, tool and address, no text. Drained every ten minutes; goes with the team |
| `ext_project_refresh_owed` | Meetings whose project minutes outside still have to be rewritten after a change -- a refresh left a copy behind, or speech was deleted (#787): a meeting id and a count of tries. Retried every ten minutes, given up on after a day; goes with the meeting |
| `ext_minutes_events` | A project's minutes as an all-day event on the meeting's day, on the calendar of the person who sent them (#788): one row per meeting, project and person, holding the event id, so sending again updates the same event, and a digest of the minutes it last received, so a refresh leaves an unchanged event alone and asks for no grant. Only that person's own grant reaches it. No text; goes with the meeting, the project and the person |
| `ext_work_reports` | That a person was sent the work-report draft for one day through one team's Slack (`work_report.py`). The primary key is the "once". No text. Its own table: `ext_daily_digests` has the same key, and its latest row is where the next morning DM counts from |

**The summary tab (S15 요약, #421, WBS 4.9).** B owns it. v1 is structured and
uses no model: `GET /summary/{meeting_id}` gives the meeting's decisions
(confirmed first, then pending; rejected left out), every action item, how many
open questions were asked and how many ambiguous agreements still wait for
their speaker, and the team's memo (`PUT /summary/{meeting_id}/note`, whole
memo, blank removes it). The tab reads it in three levels -- counts, then the
decisions and items, then their source lines on the 액션 tab. Nothing leaves,
so it serves real meetings whatever #392 decides. v2 adds a prose summary by
a cloud model over the whole meeting -- section summaries under the outbound
limit, then a summary of those -- stored in `ext_meeting_summaries` and shown
above v1's rows. It is off by default (`AUTUNE_EXTRACTION_SUMMARY_IMPL=none`)
and, like every cloud implementation in this module, refused at start-up
without `AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392`: demo meetings only until
#392 is decided.

A meeting that is processed again replaces its model-made rows —
classifications, decisions, and draft items — rather than adding a second set,
which is what makes a redelivered task safe. The one exception is the draft:
once a person has edited anything in the meeting, a rerun leaves its items
alone, because ADR 0006 makes the list theirs to finish.

**Consent that changes after the run (#518).** The consent filter reads
`participants.consented` when the run starts, and consent can be recorded
later (A's `attest_consent`). Nothing announces that (#360), so every ten
minutes `reextract_consent_changes` compares each meeting's
`ext_extraction_runs` digest with the consenting utterances now, and extracts
the meetings that differ again from the stored transcript — the same run as
the event's, so it follows the rules above and publishes `ExtractionResult`
again. A meeting extracted before the table existed has no row and is left
alone. Speech that loses consent drops out of the model's rows the same way;
what a person already edited or sent out from it waits on per-person
withdrawal (S10/S11), the second half of #518.

**A run that fails.** A run that raises is counted in
`ext_extraction_attempts` before the error goes on, and every ten minutes
`retry_failed_extractions` tries the meeting again from the stored transcript:
three attempts in all, the event's being the first. A run that goes through
ends the count. After the third failure the sweep stops and the team's Slack
channel gets one message -- the meeting's title, the count and a link to its
액션 tab -- when the team has a channel connected; the 액션 tab says it either
way, and offers "다시 추출" there, on any meeting. A run whose rows were
committed and whose `EXTRACTION_COMPLETED` could not be published is counted
too, as its own kind (`ResultNotPublishedError`, #887): the sweep publishes
the stored result again and asks no model, and the tab and the channel's
message say the items were extracted and not passed on. That request is a
row the worker takes within a minute (`run_requested_extractions`), because
the API process has no broker to queue on; it is the same run, so an item list
a person has edited is kept. A meeting with a transcript and no extraction on
record at all -- a run that failed before failures were counted, or one lost
with a worker -- is counted as failed once, half an hour after its last line
was stored and for a week, and tried the same way.

**A speaker identified after the run (#360).** A commitment by an unidentified
speaker keeps only the label ("Speaker 2"). When A later fills
`participants.user_id`, nothing announces it, so every ten minutes
`fill_identified_assignees` gives each model item from the last 30 days still
holding only the label it was drafted with the account of the one identified,
consenting speaker behind its sources, and clears the label. An item whose
assignee a person may have edited -- an edit naming an assignee field, or an
older edit row naming no fields -- or whose label a person renamed is left
alone. A confirmed item is synced to Notion, Jira and the calendar the way
the router syncs a board edit; like a board edit, no `ExtractionResult` is
published.

**A speaker corrected or undone afterwards (#929).** The same run keeps the
assignee with the speaker in the other direction too. A model item's assignee
comes from its speaker and from nowhere else, so while no person has chosen
one it is whatever a fresh extraction would write: when A moves the label to
another member, the item moves to that member; when A undoes the assignment
(#928), the item loses the account and shows the speaker label again. The
same items are left alone as above, and so is one that is done -- who
finished it is not recorded, and nothing is sent about it any more. An item
a person confirmed or started does follow: its reminders, digests, Notion
page, Jira issue and calendar event are what would otherwise stay with the
person the label was wrongly put to. Two things do not follow: a reminder
already sent for a due date is not sent again to the new assignee, and the
previous assignee's calendar event is removed only as far as their grant
still allows (`calendar_sync`).

**What earlier meetings left open (PRD 5.2, WBS 4.8).** `GET
/carried-over/{meeting_id}` answers a member of the meeting's team with the
open items -- *to do* or *in progress* -- of the team's meetings held before
this one: counts of all of them and the ten most urgent, overdue first. The
review screen opens with a popup listing them the first time a meeting is
reviewed in a browser, and keeps a one-line reminder above the board after.
Drafts still in *needs confirmation* and finished items are not carried.

`ext_action_items.due_text` is the phrase a model item's due date was read from,
for S18. It is cleared when a person sets the date themselves: the phrase no
longer explains the value (#109).

`ext_action_items.origin` is `model` or `user`. ADR 0006 makes the output a draft
the user completes, so an item somebody typed is an ordinary row rather than an
anomaly — and telling the two apart is what edit cost is measured against.

Source utterances are a table rather than a JSONB list because the detail drawer
joins them back to read the quotation, and `data-model.md` rules JSONB out for
anything you join on.

`ext_edit_events` carries no user id and must not gain one. ADR 0003 forbids
per-person metrics, and "who corrected the model most" is the same shape of data
as a speaking ratio. Its `action_item_id` clears on delete rather than cascading:
cascading would remove the evidence that the model was wrong along with the wrong
item, and the metric would improve every time somebody deleted something.

`ext_decisions` carries no owner column. ADR 0007 makes a record reachable by
`meeting_id` the meeting's, and a decision is the clearest case of it: the team
is still bound by what was settled after the person who proposed it leaves.

`ext_decision_sources` keeps a `position` so the sources come back in meeting
order without a second join. The order carries the argument — the proposal
first, the sentence that settles it last — and the statement is taken from the
last one.

Rebuilding a meeting's decisions replaces them, but a decision's `dec_` id is
derived from the meeting and the utterances it was settled in, so a rebuild over
the same labels and the same utterance ids keeps the same ids (#171). A decision
whose sources changed gets a different id — and module A mints new `utt_` ids
whenever it reprocesses a recording (#194), which changes every source — so a
caller that rebuilds still republishes `ExtractionResult`. Matching an old decision to a reworded new one is the
same-decision question, and #25 gave that to D.

`ext_action_items` references `utterances.id`. It does **not** reference any
other module's tables.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/results/{meeting_id}` | The meeting's `ExtractionResult`, built from what is stored |
| GET | `/action-items` | Filter by `meeting_id`, `assignee_id`, `status`, `due_before` (strict). Source utterance ids, never their text. Each item says its meeting's team (`team_id`) |
| GET | `/teams/mine` | The reader's own teams by name. The board across meetings (the sidebar's 액션아이템) lays the same items out at once, team by team or project by project (보기), and heads each team's board with these. A project (`GET /projects`, `GET /projects/mine`) says its `team_id`: a name is unique within a team and not across them, so where that board lists several teams' projects without their items -- the project filter and the progress strip -- each says its team's name beside its own, as the 프로젝트별 groups do, and says nothing when the projects are all of one team |
| GET | `/action-items/{id}` | One item, the text of its source utterances in spoken order, up to three lines said just before them as `context`, and the lines its summary says it used as `related` (consenting speakers only) |
| PATCH | `/action-items/{id}` | Edit or close an item |
| POST | `/action-items` | Add an item the model missed |
| DELETE | `/action-items/{id}` | Delete an item the model got wrong |
| POST | `/results/{meeting_id}/sync` | Re-sync to Notion and Jira — not built; confirming an item syncs it |
| GET | `/reviews/{meeting_id}` | What needs a person before anything is sent: decisions with their verdict, weak assents with their DM state, items still `needs_confirmation` or below the candidate line (S15, #246) |
| POST | `/decisions` | Add a decision the model missed. Confirmed, and kept through reruns |
| GET | `/decisions/{id}` | One decision and the text of the utterances it was settled in, in spoken order (S15 shows them beneath the statement), plus the same `context` |
| PATCH | `/decisions/{id}` | Confirm, reject, reword, or put back to pending |
| DELETE | `/decisions/{id}` | Delete a decision a person added; reject one the model proposed, which a rerun would otherwise bring back |
| GET | `/reviews/{meeting_id}/outbound` | Exactly what may leave for Notion, Slack or Jira: confirmed decisions and accepted items, each screened for personal data (a hit is held back in `blocked`, by id and category). The sync reads this and nothing else |

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |
| `autune.extraction.on_transcript_ready` | `autune.transcript.ready` | `cpu_heavy` |
| `autune.extraction.sync_action_item` | A person confirms an action item (`PATCH /action-items/{id}` out of `needs_confirmation`). Today it runs in the API process right after the response, as a FastAPI background task — apps/api builds no Celery app to queue it on | `default` |
| `autune.extraction.sync_decision` | A person confirms a decision (`PATCH /decisions/{id}` to `confirmed`) or adds one (`POST /decisions`). Runs in the API process after the response, like `sync_action_item` | `default` |
| `autune.extraction.send_confirmations` | After extraction | `default` |

## Slack surface

- Action-item card thread posted to the meeting channel
- A DM to each speaker with an ambiguous agreement, asking for confirmation
- A DM to an item's assignee the day before its due date and once after it
  passes (`reminders.py`, `autune.extraction.periodic.remind_due_items`, every
  ten minutes, 09:00–20:00 Korea time). To the assignee's own linked account
  and to nobody else; only for a confirmed, unfinished item whose assignee is
  an account on the meeting's team. Off by default:
  `AUTUNE_EXTRACTION_DUE_REMINDERS=true` turns it on
- A DM to each person on a Tuesday-to-Friday morning (09:00–12:00 Korea time):
  what changed on their own items since the last one and what is theirs to do
  today (`reminders.build_daily_digest`,
  `autune.extraction.periodic.send_daily_digests`). Monday has the weekly
  digest instead and a weekend has nothing. Not sent to a person who turned
  their reminders off, or on a day inside their own leave dates
  (`/me/notification-pause`), which stop Monday's digest too. Off by default:
  `AUTUNE_EXTRACTION_DAILY_DIGEST=true` turns it on
- Neither digest goes on a public holiday (`days_off.py`): the days are read
  twice a day from Google's public calendar of Korea's holidays, with no
  credentials (`autune.extraction.periodic.refresh_public_holidays`;
  `AUTUNE_EXTRACTION_PUBLIC_HOLIDAY_CALENDAR=false` stops the call), and the
  `holidays` package answers when there is no read from the last two weeks.
  When Monday is a holiday the week's digest goes on the week's first
  working day instead, and that day has no morning DM. Due-date reminders are
  not held back
- With `AUTUNE_EXTRACTION_LEAVE_FROM_CALENDAR=true` (off by default), a person
  whose own connected Google Calendar marks them out of office at that moment
  is not sent either digest; it is asked again on the next run, so somebody
  back within the sending hours gets theirs then. Out-of-office times only
  are read, and nothing is stored (`docs/architecture/privacy.md`)
- A DM to a person on a Monday-to-Friday afternoon (16:00-17:00 Korea time)
  with a draft "오늘 업무 보고" of their own items on one team, written so
  that they can paste it to that team themselves (`work_report.py`,
  `autune.extraction.periodic.send_work_reports`, the user 2026-10-07).
  Four parts, each item in the first it fits, and the rest of their open
  items as a count: 끝낸 일 (theirs, done now, status edited today), 진행한 일
  (in progress now, status edited today), 내일로 넘어가는 일 (in progress from
  before, or due today and not finished), 늦은 일 (due before today). That is
  all the edit log can say -- it keeps that a field changed and when, never
  the value or who -- so work that left no change on the board is not seen,
  and an item of theirs somebody else marked done reads as finished. Sent
  only when something of theirs was finished or moved today; plain text from
  the rows, no model; to the person and nobody else -- no channel, no lead.
  Once per person, team and day (`ext_work_reports`). The same switch and
  leave dates that stop the morning DM stop it, and so does a public holiday;
  only about items whose assignee is on the meeting's team. The hour is the
  last of 09:00-17:00, outside which the DMs made from 2026-10-07 on are not
  sent (the user); a report that did not go in it is not sent later, so the
  day it describes ends at about 16:00. Off by default:
  `AUTUNE_EXTRACTION_WORK_REPORT=true` turns it on
- Role-specific reports (Phase 2)

## AI stack

| Component | Model |
| --- | --- |
| Utterance classification | `kakaobank/kf-deberta-base` (DeBERTa, [MIT](https://huggingface.co/kakaobank/kf-deberta-base)), fine-tuned |
| Agreement verification | `klue/roberta-base` fine-tuned on KorNLI ([CC BY-SA 4.0](https://github.com/kakaobrain/kor-nlu-datasets) training data, server-only — #172) |
| Reference resolution, report generation | LLM |
| Due-date parsing | Rule-based Korean date parser plus LLM fallback |

Target non-LLM share is roughly 60%: classification and verification are models
we train, not prompts.

### Which encoder, and what is still open

The encoder is `kakaobank/kf-deberta-base`, chosen in #112 over
`microsoft/mdeberta-v3-base`. The two share an architecture (12 layers, 768
hidden, 12 heads) but not a cost. Measured on CPU (6 threads) over 256 Korean
utterances in batches of 32, `max_length` 96:

| | kf-deberta-base | mdeberta-v3-base |
| --- | --- | --- |
| Forward pass, one batch of 32 | 1.63 s | 29.9 s |
| Throughput | 19.6 utterances/s | 1.1 utterances/s |
| One 45-minute meeting (~2,400 utterances) | 2.0 min | 37.4 min |
| Tokens per Korean character | 0.485 | 0.673 |

It also trains on the English AMI data despite being a Korean model: 4,000 AMI
utterances for two epochs reached a five-way macro F1 of 0.6082 on English AMI
(#112), so the AMI loader and label mapping are not wasted.
`training.BASE_CHECKPOINT` holds the name, and `--base` overrides it for a
comparison run.

**The encoder is decided; the trained checkpoint is not.**
`AUTUNE_EXTRACTION_CLASSIFIER_CHECKPOINT` stays blank until a Korean-trained
checkpoint beats the AMI-only baseline on the Korean evaluation set (#10). Where
that checkpoint is stored, and under which terms, is open on #112. AMI is
CC BY 4.0 and needs attribution. Rows derived from AI Hub carry AI Hub's own
terms, which govern providing the data to others and taking it abroad.

### Classifier training data

Labels are produced by an LLM in a first pass over Korean meeting utterances and
then corrected by hand. Hand-labelling from nothing spends the only days this
project has for it, and labelling functions break down on exactly the two classes
that matter most here — `concern` and `ambiguous` are what feeds the NLI
confirmation step, and neither reduces to a keyword rule.

This does not spend the non-LLM budget above. That target describes what runs at
inference: it is "a design target, not a metric we measure... to keep the team
building real models rather than prompt chains" (`../product/prd.md` section 8).
An LLM that writes training labels produces a trained classifier, which is the
thing the target is asking for.

The label definitions come from the AMI Meeting Corpus rather than being invented
here, because AMI annotates the same boundaries already:

| Kind | AMI source |
| --- | --- |
| `commitment` | Dialogue act `Offer`; abstractive `actions` |
| `decision` | Extractive `decision` spans; abstractive `decisions` |
| `open_question` | The four `Elicit-*` dialogue acts |
| `concern` | Adjacency-pair `NEG`; abstractive `problems` |
| `ambiguous` | Adjacency-pair `UNC` and `PART` |

Two of those are worth knowing. `Suggest` is not a commitment — it is a proposal,
and it outnumbers `Offer` six to one, so folding it in buys noise. Polarity is not
in the dialogue-act inventory at all; `Assess` is the largest task act and carries
no sign, which is why `concern` is keyed on the adjacency pairs instead.

The table above lives in code as `autune_extraction.labeling.ami`, along with the
acts it deliberately leaves out and the reason for each. Three things read it —
the corpus loader, the LLM labelling prompt, and the hand-correction pass — and
three paraphrases of a table drift apart. All sixteen of AMI's leaf acts are
either mapped or excluded by name, because an act nobody decided about looks
exactly like an act somebody forgot.

An utterance can carry evidence from several layers at once: an `Offer` inside a
UNC adjacency pair is both a commitment and an ambiguity. `PRECEDENCE` settles
those, highest first:

```
decision  >  concern  >  open_question  >  ambiguous  >  commitment
```

AMI does not state an ordering, so this one was a judgement — and then
`scripts/ami_label_conflicts.py` measured it. Over 117,915 dialogue acts it
labels 17,876 and finds **809 contested (4.5%)**, so the ordering decides real
training data rather than a handful of edge cases.

| Rule | Cases |
| --- | --- |
| `open_question` over `ambiguous` | 406 |
| `decision` over anything | 365 |
| `concern` over the two below it | 33 |
| `ambiguous` over `commitment` | 5 |

The measurement changed the ordering. `ambiguous` originally outranked
`open_question`, on the reasoning that polarity is the better-informed layer.
The cases it produced are questions, not hedged assent — "Do we need an LCD
display?" — and an `ambiguous` label triggers a DM asking the speaker whether
they meant to commit. Asking that about a question is not a near miss.

The `ambiguous` over `commitment` rule is the "한번 볼게요" case and is right where
it fires, but it fires five times, and structurally so: polarity is a property of
a response while an `Offer` is an initiating move, so the two rarely land on one
utterance. Keep the rule; tune nothing on it.

**A decision span promotes only an act that asserts something.** The extractive
layer marks a *region* — the stretch a human selected as evidence — not one
utterance, and promoting everything inside it produced 9,835 `decision` labels
from AMI's 288 annotated decisions. A third of those were acts the table above
already excludes by name: 1,331 Fragments, 1,271 Backchannels, 730 Stalls, so
the corpus taught that "Hmm." and "Yeah." are where a meeting settles something.
`ASSERTIVE_ACTS` is the four that put something on the record — `Inform`,
`Assess`, `Suggest`, `Offer` — and membership is required in addition to the
region, never instead of it.

Each label records which layer produced it and which kinds it overruled, which is
what makes the table above producible at all.

Building the training set from an annotated corpus:

```bash
uv run --package autune-extraction python -m autune_extraction.labeling     --corpus dataset/ami_public_manual_1.6.2 --out dataset/ami
```

It writes `train.jsonl`, `validation.jsonl` and `test.jsonl` in the same format
the evaluation harness reads, and prints the per-class counts of each split.

**The split is by meeting, never by utterance.** Two utterances from one meeting
share a topic, four speakers and a vocabulary, so splitting at the utterance
level scores the model on conversations it has already read.

Meetings are stratified on whether they carry a decision layer — AMI annotates
decisions in 47 of its 139, and those meetings supply almost every `decision`
label — and then each goes to whichever split has the largest shortfall in its
neediest class. Balancing on total count alone leaves the classes uneven,
because decisions are not spread evenly even among the meetings that have them.

A meeting is not divisible, so with seventeen of them in a held-out split there
is a floor on how even this gets. The summary prints the remaining gap rather
than leaving it to be discovered as a surprising validation score.

A file it writes is training data, not an evaluation set. The evaluation set is
drawn from the team's own meetings and is what ADR 0006 measures against; the
format is shared for convenience, not because a score on AMI would transfer.

Corpora are downloaded per machine and never committed (`dataset/` is gitignored).
AMI is CC BY 4.0 and requires attribution wherever results are published. Analysis
scripts live in `modules/extraction/scripts/`.

Neither corpus is a Korean team meeting — AMI is English design roleplay, and the
Korean set is broadcast discussion. A model tuned on them has not been shown to
reach the F1 target on real meetings; an evaluation set drawn from the team's own
meetings is what would measure that gap.

## User correction

Everything the pipeline produces is a draft. ADR 0006 sets the rule: an item can
be edited, deleted, or added by hand, every item carries the utterances it came
from, and items below the confidence threshold appear as candidates rather than
being dropped. Recall is ranked above precision for that reason — a wrong item
costs a click, a missing one costs re-reading the meeting.

Corrections stay in the meeting. They update `ext_action_items` and increment the
edit-cost counters; they are never exported as training labels (ADR 0003), and
edit cost is aggregated per meeting, never per person.

A deleted item is deleted. `privacy.md` allows no soft deletes and no tombstones
holding content, and edit cost does not need one: the counter records that a
deletion happened, which is the whole of what the metric asks. Keeping the row to
remember the model was wrong would be keeping meeting content for a reason the
privacy rules do not grant.

## Metric

The classifier's macro F1 over the five kinds is what we train against and what
the harness scores, **taken on an evaluation set where utterances that are none
of the kinds appear at their real proportion**. `none` is scored and never
averaged: a none utterance called `decision` is a false positive in
`decision`'s precision, and a decision called none is a miss in its recall.
Action item F1 is derived from it and reported beside the best published figure
for the task, per ADR 0006.

A set of labelled utterances only cannot see what the model does with the rest
of a meeting. On AMI the same model scored 0.655 on one and 0.225 on the
meeting's real distribution, with 1,888 false labels per 2,400 utterances
(#149). The harness warns when an evaluation set has no `none` rows. For AMI,
`python -m autune_extraction.labeling` writes `test.jsonl` as the natural
distribution and `test_closed.jsonl` as the labelled-only split, kept for
comparison with numbers taken before `none` existed.

| Metric | Six weeks | Three months |
| --- | --- | --- |
| Action item F1 | 0.43 — matching the best published AMI result, 43.12 (ADR 0006) | above it |
| Classifier macro F1 over the five kinds, `none` present | not set (#221). The AMI dialogue-act literature reports accuracy over a 15-tag set, so it has no figure that is like for like with this metric; and the owner chose the LLM classifier (`classifier_impl=llm`) over the trained one for cost (2026-10-06), so no target is set for the trained one. The code's default is still `local` | not set |
| Items the user accepts with no edit | the first measurement is the baseline | improve on it |

```bash
uv run --package autune-extraction python -m autune_extraction.eval \
    --eval-set dataset/extraction_eval.jsonl \
    --predictions runs/<model>.jsonl
```

The evaluation set is drawn from real meetings and is never committed. The
harness scores a predictions file rather than loading a model, so a run can be
rescored without a GPU and the metric means the same thing across model
versions.

## Privacy notes

- Only what an issue needs goes to Notion or Jira: the action description,
  assignee, and due date. Never the full transcript.
- The LLM used for reference resolution receives masked text only, and the
  smallest window that resolves the reference.
- A failed copy to an outside tool is remembered by its kind and its time
  only (`ext_sync_failures`, #680): the service's own message may echo what
  was sent and is not stored or logged. Notion and Jira are the team's
  connections and their failures are shown to the team. A calendar is one
  person's: what an item lacks (not confirmed, no date, no account for an
  assignee) is said to anybody, and everything past that -- an event being
  there, none being there, a failed calendar copy -- only to the assignee,
  since each says whether that person connected a calendar.
- Confirmation DMs go to the speaker, never to a channel.
- Due-date reminders go to the item's assignee, never to a channel, a manager
  or the person who made the item, and nothing counts or ranks what a person
  has missed. The message carries the item's description, its due date, the
  meeting's title and a link — no utterance.
- `GET /action-items/{id}` is the only route in this module that returns
  utterances verbatim: the drawer asks for one item's quotation when it opens,
  and the list returns utterance ids. The list is still meeting content — an
  item's `description` is drawn from what was said and `assignee_label` is a
  person's name — so no response of this module may be forwarded outside our
  infrastructure on the grounds that it quotes nobody. `check_outbound` catches
  the shapes of personal data, not a Korean name or the sentence that settled a
  decision.
