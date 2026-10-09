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
| Slack | DMs to the person concerned (confirmation, reminders, digests); to the team's channel, a project's minutes when a person sends them and one notice when a meeting's extraction is out of tries | — |

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
   the person confirming reads one against the other. A row made from one
   sentence of a long turn is shown that sentence and not the turn (*The part
   of a turn*, under Tables). Only the tidied line, as
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
   발화" on S15). With `classifier_impl=llm` **every decision the classifier
   wrote a line for shows that line**, and cites the lines it took a word
   from, found the same way: the owner asked for each action item and
   decision as one line (2026-10-06), and the line came with the label at
   no further request. A decision with no such line is sent to the
   resolver, **whatever its settling turn says** (2026-10-08). Before that
   the resolver was asked only when the turn was short or pointed at
   something said before ("그렇게 하죠"), since for the others it mostly changed
   the ending; but those were the rows that then stayed as said, or that
   `noun_form.tidy` turned into "…결정 예정" when the meeting had decided. On
   twelve invented decisions with no classifier line all twelve write-ups
   passed the resolver's checks and read right -- one meeting, one run, not
   a rate. It costs a request for about every six such decisions. A
   write-up that fails a check, or only repeats the turn, leaves the tidied
   line, as does a resolver that cannot cite.

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

   A second fixed rule, also before this step and also with no model, works
   the other way (module B's owner, 2026-10-09: a launch date, a settled date
   or a deadline said in a meeting is a decision). A line the classifier left
   unlabelled becomes a pending `decision` at confidence 0.9 when one of its
   sentences has a milestone word (출시, 마감, 오픈, 배포, 확정, 론칭, 런칭,
   릴리스, 릴리즈, 데드라인, 납기, 기한) and a date: one the due-date reader
   takes, or one something was set to ("계약 갱신일은 11월 15일로
   확정됐습니다", where the past verb is the settling). It never touches a line
   that has any kind -- a promise with a deadline stays one item -- nor a turn
   read in pieces, a question, or a sentence that says the date is open (아직,
   미정); a date said of the past ("원래 마감은 10월 30일이었죠") is left
   alone. The cost of a rule that reads words: a date somebody recalls or
   worries about can be taken ("원래 마감은 10월 30일로 잡혀 있었죠", "출시가
   금요일인데 걱정이네요"), and a person rejects it on the review screen. Until
   then it is a pending decision like any other and reaches D and E in
   `ExtractionResult`.
5. **Build decision entities** — group the utterances classified as decisions
   into `Decision` records with a `dec_` id and the statement as settled. One
   decision often spans several utterances. **Module D depends on this**: it is
   what a decision lineage is keyed on, and a `Classification` alone is not
   enough. See `../architecture/contracts.md`, "The B → D boundary".

   **Which decision turns are one decision** (the owner, 2026-10-09). Turns
   said back to back are one decision only while at most one of them says
   something of its own: a turn with content, then "네 그렇게 하죠", is one
   decision, and the assent belongs to the one it follows. A second turn with
   content starts a new decision, so a wrap-up that lists three decisions is
   three rows, and a date said for one is not attached to the next. "Content"
   is at least twelve characters (`decisions.MIN_SUBSTANCE`) once the words
   that only point ("그렇게", "그대로") are taken out
   (`decisions.says_something`). The cost, accepted: one decision said twice
   in full sentences is two rows, and a person removes one. Before this every
   decision turn in a row was one decision, and three decisions read out
   together became one row with the last one's date.

   **A date that is what was decided is not the decision's deadline.** "배포를
   화요일로 바꾸기로" decides a day, and "매주 월요일에 하기로" a day that
   repeats; neither is due by anything. In a decision a date followed by
   (으)로, or said as a day that repeats (매주, 매달, 매일, 격주 and the
   like before it, or 마다 after it), is left out of the deadline
   (`slots.parse_due(decided=True)`); "10월 20일에 내기로" and "다음 주
   금요일까지" are read as before, and "금요일까지로" stays a deadline. An
   action item's date is read as it always was.
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
| `ext_action_item_sources` | Which utterances an item came from, and for a row made from one sentence of a long turn, where that sentence is in the utterance: two character offsets (`excerpt_start`, `excerpt_end`), never the words. A row outlives its utterance with `utterance_id` NULL (#379) and the same trigger clears its offsets then |
| `ext_decision_related` | The other lines of the meeting a decision's summary was written from, as the model said it used them; shown beneath the summary, never read by D |
| `ext_action_item_related` | The other lines of the meeting the item's summary was written from, as the model said it used them (`LlmResolver`); shown beneath the summary, never read by D or E |
| `ext_edit_events` | One row per correction, and one per close without finishing (`closed`, which is not a correction and is left out of edit cost). Counts only — no person on it |
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
| `ext_decisions` | Decision entities, their statements and source utterances. `origin` is `model` or `user`; a rerun rebuilds only the model's, and of those only the ones no person has confirmed, rejected or reworded (see "Rebuilding a meeting's decisions" below) |
| `ext_decision_sources` | Which utterances a decision was settled in, in order, and the same two offsets for a decision settled in part of a long turn. A row outlives its utterance with `utterance_id` NULL (#400), as an action item's does: readers list the sources that exist and say how many were deleted (`deleted_source_count`), and the row keeps its `position` and nothing of the line -- no id, speaker, time or words, and no offsets: a trigger on the table clears `excerpt_start` and `excerpt_end` whenever the row has no `utterance_id`, so every path that deletes an utterance is covered without this module being told |
| `ext_decision_reviews` | A person's verdict on each proposed decision (pending, confirmed, rejected) and an optional rewording, keyed by `dec_` id so a rerun over the same sources keeps it (#246). A row that says something -- a verdict or a rewording -- also keeps its decision through a rerun that would group the lines differently; one put back to pending with no rewording does not. No reviewer column |
| `ext_extraction_attempts` | One row per meeting whose extraction failed, whose stored result could not be published, or that a person asked to extract again: failures in a row, the class of the last error (never its message), when, when the team's Slack channel was told, and the request the worker takes. Deleted with the meeting |
| `ext_extraction_runs` | One row per extracted meeting: a digest of the consenting utterances the last run read, and when (#518) |
| `ext_meeting_notes` | The team's memo on a meeting's summary tab (S15 요약, #421). Free text a member typed; no author column; a blank memo is no row |
| `ext_meeting_summaries` | A meeting's summary written by a cloud model, only with `AUTUNE_EXTRACTION_SUMMARY_IMPL=llm` (#421 v2): an overview, points one per line, the model, and a digest of the lines it was written from. One per meeting, deleted with it. A summary whose lines have changed is not shown and is deleted by the next run; deleted speech deletes it at once. A row with `too_long` set is not a summary: it holds no text and says only that the meeting, as those lines, needs more model calls than one meeting is allowed, so the tab can say so and the same lines are not tried again; it is deleted by the same rules |
| `ext_forgotten_utterances` | The ids of utterances a person deleted, from B's speech hook until module A has removed the rows, so no summary is written from them in between (#782). An id and a time, nothing said; each row goes with its utterance |
| `ext_weekly_digests` | That a person was sent Monday's DM of their own open items for one week through one team's Slack (#792). The primary key is the "once"; the message is not kept |
| `ext_daily_digests` | That a person was sent the morning DM for one day through one team's Slack. The primary key is the "once", and the latest row's time is where the next DM's "since the last one" starts; the message is not kept. Goes with the person and with the team |
| `ext_meeting_notices` | That the notice after one meeting was sent to a person, or refused by the outbound check -- a refused one keeps its row so it is reported once and not built again, and the row does not say which. The primary key is the "once"; no text and no count. Goes with the meeting (its retention expiry included) and with the person |
| `ext_notification_pauses` | One range of days a person set for themselves on which the morning DM and Monday's DM are not sent. Dates only; read and written by that person alone, shown to nobody else, deleted once the range has ended. Goes with the account |
| `ext_public_holidays` | The public holidays no digest goes on: one row a day, as Google's public calendar of Korea's holidays listed it at the last read, with that read's time (`days_off.py`). Replaced whole on every read; not used once the newest read is two weeks old. Dates of public record -- nothing about a person, a team or a meeting |
| `ext_projects` | A team's projects as its members name them: a name, other names people say for it, and optionally its own Jira project key (#786). Typed by a member, not derived from speech; goes with the team. `ext_decisions` and `ext_action_items` point at one through `project_id` |
| `ext_materials` | The Google Drive files a team keeps on its 자료 screen (#817): a title a member typed, the file's id and which Google editor it belongs to (`materials.py`). Not the file and not the link as pasted -- Autune reads nothing of the file, holds no Drive permission, and builds Google's address from the id where it is shown. No column names a person. The title is stored as typed, like a meeting's title; neither it nor the file id is logged. A file once per team, at most 200 a team -- registrations at once included: the count and the insert run under a per-team advisory lock (`materials.lock_shelf`); any member deletes one; goes with the team |
| `ext_project_sends` | Where a project's minutes for one meeting were sent, per tool (#787): the Notion page id, the Slack message as `channel:ts`, or the Jira issue key, so sending again updates that copy, and a digest of the minutes it last received, so a refresh leaves an unchanged copy alone. Addresses and a hash, no text; goes with the meeting and with the project |
| `ext_project_send_cleanup` | Copies of project minutes still to take out of a team's tool after their meeting or project was deleted, and half a Notion page that could not be taken back (#787): team, tool and address, no text. Drained every ten minutes; goes with the team |
| `ext_project_refresh_owed` | Meetings whose project minutes outside still have to be rewritten after a change -- a refresh left a copy behind, or speech was deleted (#787): a meeting id and a count of tries. Retried every ten minutes, given up on after a day; goes with the meeting |
| `ext_minutes_events` | A project's minutes as an all-day event on the meeting's day, on the calendar of the person who sent them (#788): one row per meeting, project and person, holding the event id, so sending again updates the same event, and a digest of the minutes it last received, so a refresh leaves an unchanged event alone and asks for no grant. Only that person's own grant reaches it. No text; goes with the meeting, the project and the person |
| `ext_work_reports` | That a person was sent the work-report draft for one day through one team's Slack (`work_report.py`). The primary key is the "once". No text. **A row lives for its day only**: the draft goes only on a day the person finished or started something, so a kept row would say which days they worked, and the sending task deletes every earlier day's row on each run (`work_report.forget_past_days`). Its own table: `ext_daily_digests` has the same key, and its latest row is where the next morning DM counts from |

**The summary tab (S15 요약, #421, WBS 4.9).** B owns it. v1 is structured and
uses no model: `GET /summary/{meeting_id}` gives the meeting's decisions
(confirmed first, then pending; rejected left out), every action item, how many
open questions were asked and how many ambiguous agreements still wait for
their speaker, and the team's memo (`PUT /summary/{meeting_id}/note`, whole
memo, blank removes it), with the meeting's own title and start so the page
can be headed when the meeting has no row to take them from. Nothing leaves,
so it serves real meetings whatever #392 decides. v2 adds a prose summary by
a cloud model over the whole meeting -- section summaries under the outbound
limit, then a summary of those -- stored in `ext_meeting_summaries`. It is off
by default (`AUTUNE_EXTRACTION_SUMMARY_IMPL=none`) and, like every cloud
implementation in this module, refused at start-up without
`AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392`. Which meetings may go through a
deployment that sets that flag is an operating rule and not a check: on the
team's dev site its own meetings only, none with a participant from outside
the team (#392, 2026-10-05), and on a real service none until #392 decides
that -- `../engineering/environments.md`, "The classifier's one external
option is opt-in". A server that sets any such implementation says the rule to
the person putting a meeting in, both halves of it: two sentences above the
consent row of the upload form ("우리 팀 자신의 회의만 올려 주세요. 팀 밖
사람이 참석한 회의는 올리지 마세요.") and of the live gate ("우리 팀 자신의
회의만 녹음해 주세요. 팀 밖 사람이 참석하면 녹음하지 마세요."), drawn only
where `GET /cloud-model` answers true. It is a notice and gates nothing.

**The tab is the meeting's minutes as one document, and "회의록 복사" copies
that page** (the owner, 2026-10-09; `features/actions/minutes.ts`). One page
model, `minutesOf`, is drawn by the tab and written as plain text by
`minutesText`, so what is on the screen is what lands on the clipboard. Before
this the two were built apart and listed different things: the tab had counts,
candidates and a line of what was said under each row; the copy had none of
them. The page, top to bottom:

- "회의록 — {title}" and the day the meeting began, "2026년 10월 8일 (목)", in
  the reader's time zone.
- v2's summary, where the deployment wrote one, under a heading that says a
  model wrote it -- "요약 · AI 작성" on the tab, "요약 (AI 작성)" in the copy --
  so the label goes wherever the paragraph is pasted.
- 결정 사항, numbered, in the order the route gives them. One nobody has
  confirmed is listed and marked "(자동 추출)".
- 액션, numbered: the item's sentence, then who, the due date and where it
  stands, each set off by a dot -- "… — 김민경 · 10월 13일 (화) · 진행 중". The
  state is not in brackets: the date already ends in them. A due date is
  written as the date line writes a day; its year is written only when it is
  not the meeting's, or the page has no date line. "진행 전" is not said,
  since it is every item a meeting has just made; an item waiting for
  confirmation says "확인 필요". The tab alone adds "기한 지남" to an item past
  its date.
- 메모, the team's own, edited in place. A change is in the copy once it is
  saved.

Left off the page, on the tab and in the copy alike: **candidates** -- the
model was not sure they were items, and minutes that listed one would state a
guess as an outcome -- and **every quotation**. The line of what was said
(`summary`) is no longer shown here; an item's source lines are read one item
at a time on the 액션 tab, and a page meant to be pasted into a chat or a wiki
is where a transcript should not follow. Under the page the tab says how many
candidates, open questions and unanswered ambiguous agreements it left out,
and links to the 액션 tab. The per-project tool (#787) stays below that,
unchanged; its rows still call an action item "할 일" (#1036).

Only the page's own lines are formatted. A date inside a stored sentence --
a decision that ends "(담당 도윤재, 기한 2026-10-13)" -- is that sentence's
and is shown as stored.

The written summary names no person (#1070). The lines go to the model without
their speakers, as for every model step here, so it cannot know who said a
first-person line: measured on three invented meetings (2026-10-08) it gave a
task to the wrong person in 2 of 15 names. Sending the speaker would be new
data leaving the module, and a stored summary saying who proposed and who
objected would be one person's stance on a decision
(`docs/architecture/privacy.md`, #168). So both prompts ask for no person, a
sentence of the last answer that still carries a `[사람N]` placeholder or a
speaker's own first person is dropped, and no name is put back; who took each
task is in the item rows under the paragraph, where a person confirms it. The
overview loses only such sentences, and the summary only when none is left.
Its points are at most seven, kept by kind (decision, task, open question,
discussion: one of every kind before a second of any) and shown in that order.
A meeting that needs more than twelve calls gets no summary; the tab says the
meeting was too long for one, from the `too_long` row.

Known limits, as measured, to weigh before the summary is switched on anywhere:

- A name that is not on the team's roster was never replaced by a placeholder,
  so the check does not see it. A per-meeting guest (#836) is such a name.
- A meeting with many decisions shows decisions only (two of three measured);
  an open question is then in the overview or nowhere. A share per kind in
  the prompt was tried and dropped: it changed the kinds and made the model
  call three decided things undecided, each against a board row it was given.
- The keeping by kind was not exercised by a real answer: after the prompt
  change the model wrote 7, 6 and 7 points. Tests show what it does past seven.

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

With `classifier_impl=llm`, a model answer that cannot be read is not taken
for "nothing found". An answer is read when it holds a JSON object whose
`labels` is an object or names nothing: `{"labels": {}}` is what the
instructions ask for when no line qualifies, and it is a success, as are `{}`,
an empty list and `null` in its place. It cannot be read when the request was
refused, no candidate came back, the candidate had no text, the text holds no
JSON object, or `labels` holds something that is not an object -- a list of
kinds, a string -- which nobody can map to lines. That window is asked once
more at once. The log line carries the cause and the provider's reason word,
never the answer.

What happens next depends on the other windows (the user, 2026-10-08). When no
window of the meeting could be read, the call raises `UnreadableAnswerError`
and the run is a failure, counted and retried as above. When some were read,
the run goes through with what they held: the rows are stored and published as
any run's, the unread windows' lines carry no label, and in the place of
ending the count the run adds one to it with the reason `PartlyUnread`. That
count is what makes the sweep run the meeting again, three runs in all, and
`ExtractionState.partly_unread` is what the 액션 tab reads to say, over the
rows, that part of the meeting was not read and items may be missing. The
reason stays until a run reads every window, also through a rerun that fails
outright, and the line with it once the tries are spent. The team's channel is
not told: its message is about a meeting that could not be extracted.

Which windows were unread is not kept, only that some were, so every rerun
asks about the whole meeting and follows the rules of any rerun: a meeting a
person has corrected keeps its items, a confirmed row stays, and what is
stored is the latest run's. A rerun that reads a different part can therefore
drop an unconfirmed row an earlier run had; the line over the board is up for
as long as that can be the case.

**A meeting with no rows.** An empty 액션 tab says which of four things is
true, from `GET /meetings/{id}/extraction` (`ExtractionState`, B's own schema):

| State | When | The tab says |
| --- | --- | --- |
| `in_progress` | Lines are stored; no run and no failure is on record; the newest line is under half an hour old | The extraction is going. It asks again every five seconds and reads the board and the decisions when the run is in |
| `overdue` | The same, and the newest line is half an hour old or more | The extraction has not happened, and points at "다시 추출" |
| `read_nothing` | The last run was allowed to read none of the meeting's lines | No consent is on record, so nothing was read; B extracts again by itself once it is |
| none of them | A run went through | No item and no decision was found |

"In progress" ends on the clock the sweep above uses, to the instant: the
moment the sweep would count the meeting as failed is the moment the tab stops
saying a run is going. A failure on record ends it sooner, and the tab's
failure line says that instead. `read_nothing` is about the meeting as a whole
and names nobody; it is false as soon as one line was read, so a meeting where
only some speech was out shows its items and no line about consent.

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

**The part of a turn.** One person talking for a minute is one utterance. The
classifier reads a turn over 300 characters sentence by sentence
(`pipeline.llm.said_lines`), and each promise or decision in it becomes a row
of its own, made from that sentence and citing the utterance. Both source
tables say where the sentence is: `excerpt_start` and `excerpt_end`, character
offsets into the text module A stored in `utterances.text`, already masked. No
word is copied into an `ext_` table — the reason the tables hold links and not
text — so deleting the utterance leaves nothing to cut, and no second copy to
find. What a reader is shown is cut from the stored text when they ask
(`excerpt.cut`), which makes it what was said and nothing a model wrote; offsets
that do not fit the text they are read against show no part rather than a wrong
one. Both are NULL for an utterance used whole, and for every row written
before the columns existed: those are quoted whole, as they were, until the
meeting is extracted again. A decision settled over two sentences of a turn has
one span, from the first to the second.

With `classifier_impl=llm` the part can be narrower than a sentence, and an
utterance too short to be cut has one as well. The request that labels a line
also asks which of its words carry the promise or the decision (`parts`,
2026-10-08), and the answer only chooses where the cut falls. The words are
looked for in the line as it was said, character for character, whitespace
aside (`llm.usable_part`), and again in the stored utterance, inside the
sentence the line was (`excerpt.quoted`); what is recorded is the same two
offsets and no word. Words that are not there -- reworded, shortened, another
line's -- are not used, and neither are words that are all of the line: the
part is then the sentence, and for an utterance that was not cut, the whole of
it. A decision whose members name words in two places has one span, from the
first of them to the last. A member that names no words contributes its
sentence, or the whole utterance when it was not cut. The `local` and `fake`
classifiers name no words. Measured on invented meetings only, the numbers in
`INSTRUCTIONS`' docstring: every part returned was in its line (282 of 282),
and a turn of about 360 characters was quoted as about 38 where its sentence
is about 65. A transcript without sentence ends and a real meeting were not
measured.

The offsets are counted on one text and are dropped when it changes. A
transcript correction (#586) clears an item's, since the item is kept and the
line it cites was rewritten; a decision's are written again on every run, and
cleared when the run finds no part.

Three screens show the part and nothing outside Autune does. The detail drawer
(S18) and the decision list (S15) quote it as the evidence, with "전체 발화 보기"
opening the whole turn in place. The card's line beneath the description
(`ActionItemRead.summary`) is what was said -- the part, when one is recorded --
whenever the description is a model's sentence, so the sentence has the words
it stands for under it; a card is two lines at most for each. The 요약 tab
showed the same line beneath each decision and item until 2026-10-09; it is
the minutes as a document now, and the minutes quote nothing (see "The summary
tab" above). Notion, Slack and Jira carry what they carried before.

Rebuilding a meeting's decisions replaces them, but a decision's `dec_` id is
derived from the meeting and the utterances it was settled in, so a rebuild over
the same labels and the same utterance ids keeps the same ids (#171). A decision
whose sources changed gets a different id — and module A mints new `utt_` ids
whenever it reprocesses a recording (#194), which changes every source — so a
caller that rebuilds still republishes `ExtractionResult`. Matching an old decision to a reworded new one is the
same-decision question, and #25 gave that to D.

**A decision a person confirmed, rejected or reworded is not rebuilt** (the
owner, 2026-10-09; `service._marked_decisions`). While every line it was made
from can be read, it keeps its `dec_` id, its sentence, its sources, its review
and its Notion page, however the run would group those lines now -- a label
the classifier gave differently this time, or a grouping rule that changed
since the row was made. No new decision is made from a line such a row holds:
a run of decision turns that is partly held is built from the free turns only,
and only if one of them says something of its own, so assent to a kept
decision does not become a row. A decision nobody marked is rebuilt as above,
and a review put back to pending with no rewording is no mark.

Two things still move a kept row:

- **A line of it was corrected since** (a PII report, #586). The model's
  sentence is read again from the row's own lines, so a word masked since does
  not stay in it; the id and the review are kept. A person's rewording is not
  touched and is flagged "출처 발화가 정정됨 · 확인 필요", as before. A row
  confirmed without a rewording stays confirmed with the re-read sentence and
  is not flagged, and its Notion page follows -- the rule #586 already had for
  a decision whose id did not change.
- **A line of it was deleted, or its speaker's consent was withdrawn.** It is
  a decision like any other again: rebuilt from what can still be read, or
  gone.

Rows stored before this rule change only when their meeting is extracted
again: a row that joined several decisions and that nobody marked is split
into new ids then, and a marked one stays as it is.

`ext_action_items` references `utterances.id`. It does **not** reference any
other module's tables.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/results/{meeting_id}` | The meeting's `ExtractionResult`, built from what is stored |
| GET | `/action-items` | Filter by `meeting_id`, `assignee_id`, `status`, `due_before` (strict). Source utterance ids, never their text. Each item says its meeting's team (`team_id`) |
| GET | `/cloud-model` | `{"in_use": bool}`: whether this server sends meeting text to a cloud model -- any of the classifier, resolver, summary or NLI switches set to a cloud implementation, the same list the start-up refusal walks. One fact about the deployment, the same for every signed-in caller, and nothing else: no implementation or model name, nothing about the key. The upload form and the live gate ask it to decide whether to show #392's operating rule above their consent row (`OwnTeamMeetingsNotice`, mounted by the two pages); a notice, which checks nothing |
| GET | `/teams/mine` | The reader's own teams by name. The board across meetings (the sidebar's 액션아이템) lays the same items out at once, team by team or project by project (보기), and heads each team's board with these. A project (`GET /projects`, `GET /projects/mine`) says its `team_id`: a name is unique within a team and not across them, so where that board lists several teams' projects without their items -- the project filter and the progress strip -- each says its team's name beside its own, as the 프로젝트별 groups do, and says nothing when the projects are all of one team |
| GET | `/action-items/{id}` | One item, the text of its source utterances in spoken order (each with `excerpt`, the part of it the item was made from, when one is recorded), up to three lines said just before them as `context`, and the lines its summary says it used as `related` (consenting speakers only) |
| PATCH | `/action-items/{id}` | Edit or close an item |
| POST | `/action-items/{id}/close` | Close a confirmed, open item that will not be finished (#856, #1077) -- what "끝내지 않고 닫기" in the detail window calls. Not a `PATCH` of the status: the item ends `done` either way, and the `closed` event is what tells a close from finished work. Answers with the item as `PATCH /action-items/{id}` does (`ActionItemRead`). Refused 409: an item still waiting for confirmation, one already finished, one already closed. An item the reader may not see is the 404 an unknown one gets. Copies outside follow as after any change of status |
| POST | `/action-items` | Add an item the model missed |
| DELETE | `/action-items/{id}` | Delete an item the model got wrong |
| POST | `/results/{meeting_id}/sync` | Re-sync to Notion and Jira — not built; confirming an item syncs it |
| GET | `/sync-log?team_id=` | S28's 동기화 기록, for a member of the team (anybody else gets the 404 an unknown team gets). Two lists about the team's action items, newest first, thirty of each: the copies that failed and still stand (`ext_sync_failures`: the item, its meeting, the system, the kind, the time) and the latest copies that were made (the Notion page or Jira issue with its link, from `ext_external_refs`; the reader's own calendar events, from `ext_calendar_events`, with no link). Not a log of every attempt: a failure leaves once a later copy goes through, and a copy's time is when it was first made. A claim with no page, issue or event yet is not listed. Decisions' pages and project minutes are not in it. A meeting past retention shows nothing |
| GET | `/materials?team_id=` | The Drive files the team keeps on its 자료 screen, the newest first (#817). Members of the team only |
| POST | `/materials?team_id=` | Register one: a title and a pasted link. Only a Google Drive or Docs file link is taken (the rules of `apps/web/src/shared/drive/driveLink.ts`), and only the file's id and kind are kept; 409 for a file the team already keeps. Any member |
| DELETE | `/materials/{id}?team_id=` | Take one off the team's shelf. Any member; the Drive file is not touched |
| GET | `/reviews/{meeting_id}` | What needs a person before anything is sent: decisions with their verdict, weak assents with their DM state, items still `needs_confirmation` or below the candidate line (S15, #246). A confirmed decision carries `held_back` (#1133): true when its statement holds something that looks like personal data -- a phone number, an address, an id number -- so the clients' check would refuse its copy to Notion and Jira. A boolean worked out when the row is read, by the same pattern check as the row below, with neither the value nor its category; S15 says on that row that it was not sent and that rewording sends it, and it clears with the rewording that removes the value. Not a record of a send that failed: it is true of a team with no tool connected too. A typed text is checked for patterns only, so a name does not set it |
| POST | `/decisions` | Add a decision the model missed. Confirmed, and kept through reruns |
| GET | `/decisions/{id}` | One decision and the text of the utterances it was settled in, in spoken order, each with the same `excerpt` (S15 shows them beneath the statement), plus the same `context` |
| PATCH | `/decisions/{id}` | Confirm, reject, reword, or put back to pending |
| DELETE | `/decisions/{id}` | Delete a decision a person added; reject one the model proposed, which a rerun would otherwise bring back |
| GET | `/reviews/{meeting_id}/outbound` | A read of what of a meeting would go to Notion, Slack or Jira and what would not: confirmed decisions and accepted items, each screened for personal data with the pattern check the integration clients run (`find_unmasked`); one that fails is in `blocked`, by id and category, and not in the lists. **It is a read, not the gate** (#1133). No sync reads it: each copy -- to Notion, Jira, Slack, a calendar -- reads the row it sends, and what stops a text there is the client's `check_outbound` on the request itself, at every exit (`autune_integrations`, `HttpClient.request`); a request it refuses is not sent, and the sender reports the refusal by id, never by the text. This was written as the one list every sender would read (#30) and the senders were never moved onto it. Its readers are this route, which no screen calls today, and the agent's `meeting_action_items` and `meeting_decisions` tools, which quote only what it lists and count the rest. When a text a person typed should be checked -- as it is stored, or as it leaves -- is open on #1130 |

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |
| `autune.extraction.on_transcript_ready` | `autune.transcript.ready` | `cpu_heavy` |
| `autune.extraction.sync_action_item` | A person confirms an action item (`PATCH /action-items/{id}` out of `needs_confirmation`). Today it runs in the API process right after the response, as a FastAPI background task — apps/api builds no Celery app to queue it on | `cpu_heavy` by its name; nothing queues it today -- every caller runs it in process |
| `autune.extraction.sync_decision` | A person confirms a decision (`PATCH /decisions/{id}` to `confirmed`) or adds one (`POST /decisions`). Runs in the API process after the response, like `sync_action_item` | `cpu_heavy` where the module queues it itself (`_follow_corrections`, `_extract` and `forget_deleted_speech` in `tasks.py`); the confirmation in this row is not queued |
| `autune.extraction.periodic.ask_confirmations` | Beat, every five minutes (step 6 of the pipeline). Not chained after extraction: a speaker who links Slack later is still asked within the window | `cpu_heavy` |

The Queue column is where `autune_core.celery_app.TASK_ROUTES` sends the name:
every `autune.extraction.*` task goes to `cpu_heavy`. This table is not the
whole list -- `tasks.py` declares the rest, most of them beat tasks named
`autune.extraction.periodic.*`.

## Slack surface

- To the team's connected channel, two kinds of message and no thread. A
  project's minutes for one meeting, when a person sends them (#787,
  `project_send.py`); sending again rewrites that message in place. And one
  notice when a meeting's extraction is out of tries: its title, the count and
  a link to its 액션 tab. No item is posted on its own and nothing is
  threaded: an item reaches the channel only as a line of its project's
  minutes (its sentence, assignee and due date), and not while it waits for
  confirmation
- A DM to each speaker with an ambiguous agreement, asking for confirmation
- A DM to an item's assignee the day before its due date and once after it
  passes (`reminders.py`, `autune.extraction.periodic.remind_due_items`, every
  ten minutes, 09:00–20:00 Korea time). To the assignee's own linked account
  and to nobody else; only for a confirmed, unfinished item whose assignee is
  an account on the meeting's team. Off by default:
  `AUTUNE_EXTRACTION_DUE_REMINDERS=true` turns it on
- A DM to each person on a Tuesday-to-Friday morning (09:00–12:00 Korea time):
  what changed on their own items since the last one (done, closed without
  being finished -- said apart, see the close below -- and newly held) and
  what is theirs to do today (`reminders.build_daily_digest`,
  `autune.extraction.periodic.send_daily_digests`). Today's part names, after
  what is late and what is due today, each item of theirs that is in progress
  or has no due date and that nobody has touched for five days or more
  ("6일째 그대로"), longest first -- `reminders.STALLED_AFTER_DAYS`, counted
  from when the item was made or last edited. A not-started item whose date
  is still ahead is not named. Monday has the weekly
  digest instead and a weekend has nothing. Not sent to a person who turned
  their reminders off, or on a day inside their own leave dates
  (`/me/notification-pause`), which stop Monday's digest too. Off by default:
  `AUTUNE_EXTRACTION_DAILY_DIGEST=true` turns it on
- Those leave dates on the person's own Google Calendar, only when they tick
  "내 Google 캘린더에도 추가" beside them (`leave_calendar.py`, 2026-10-06):
  `PUT /me/notification-pause` takes `on_calendar` -- `true` the tick,
  `false` the box unticked, and left out by a screen that drew no box, which
  leaves the calendar as it stands: an event already there moves with the
  dates and keeps its id, and none is made where there is none -- nor in
  place of an event the person deleted in Calendar: that takes a tick, and
  such a save answers `off` and drops the id (lsh2217's
  review of #922: read as `false`, a dates-only save by a person whose
  calendar was disconnected dropped the id, and the next ticked save made a
  second event) -- and
  writes, moves or removes one private all-day event titled "휴가" in the
  request, through the person's own grant; the answer's `calendar` says what
  happened (`added`, `removed`, `removal_queued`, `not_connected`,
  `not_removed`, `failed`, `off`) and the dates are saved whichever it is.
  `not_connected` is an event that was not put there; `not_removed` is one
  that is there and cannot be taken off, because the calendar is no longer
  connected -- the person is told to delete it themselves. **Google is asked
  with no transaction open** (mminjae97's review of #922): the dates are
  committed first, then the calendar is asked, holding neither the row's lock
  nor a connection, then the answer is written in a second transaction. One
  save at a time is at the calendar for a person -- the first leaves
  `calendar_claimed_at` on the row, and a save that finds a claim younger
  than two minutes (`leave_calendar.CLAIM_FOR`) is refused with 409 and
  changes nothing, the dates included -- so a double press still makes one
  event. A claim left by a process that died stops holding after those two
  minutes. A save that comes back to find its claim taken over, or the row
  gone, writes nothing and queues the event it made for removal. The read says
  `calendar_connected` so the screen draws the box only then. The event's id
  is kept on the pause (`calendar_event_id`) and goes with it after the last
  day; the event itself then stays on the calendar. Rules and what is said
  to the person: `docs/architecture/privacy.md` section 6
- A DM to a person soon after a meeting is processed, when the pipeline has
  put work of that meeting on them (`meeting_notice.py`,
  `autune.extraction.periodic.send_meeting_notices`, every five minutes): the
  meeting's title, **how many** drafts wait for their confirmation, and a link
  to that meeting's 액션 tab. No draft's text or date -- unconfirmed content
  does not leave (#246) -- while an item already confirmed is named with its
  date. Once a person and meeting (`ext_meeting_notices`); only somebody on
  the meeting's team now; 09:00-17:00 Korea time on a working day, and what
  could not go then goes at 09:00 on the next working day. Stopped by the
  reminder switch and by a person's own leave dates. Off by default:
  `AUTUNE_EXTRACTION_AFTER_MEETING_NOTICE=true` turns it on
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
  Five parts, each item in the first it fits, and the rest of their open
  items as a count: 끝낸 일 (theirs, done now, status edited today, and not
  closed), 끝내지 않고 닫힌 일 (closed today without being finished -- see the
  close below; alone it sends no draft), 진행한 일 (in progress now, status
  edited today), 내일로 넘어가는 일 (in progress from before, or due today and
  not finished), 늦은 일 (due before today). That is
  all the edit log can say -- it keeps that a field changed and when, never
  the value or who -- so work that left no change on the board is not seen,
  and an item of theirs somebody else marked done reads as finished. Sent
  only when something of theirs was finished or moved today; plain text from
  the rows, no model; to the person and nobody else -- no channel, no lead.
  Once per person, team and day (`ext_work_reports`) -- and that row is
  deleted once its day has passed, by the same task on every run (also with
  the feature off, and outside its hour): that the draft went says the
  person worked that day, and no such record is kept (mkkim68, review of
  #954). The task returns a count and logs a failure by team, for the same
  reason. The same switch and
  leave dates that stop the morning DM stop it, and so does a public holiday;
  only about items whose assignee is on the meeting's team. The hour is the
  last of 09:00-17:00, outside which the DMs made from 2026-10-07 on are not
  sent (the user); a report that did not go in it is not sent later, so the
  day it describes ends at about 16:00. Off by default:
  `AUTUNE_EXTRACTION_WORK_REPORT=true` turns it on
- An item can be **closed without being finished** (#856; the user,
  2026-10-07) -- dropped, overtaken, no longer needed. There is no cancelled
  status: a close makes a confirmed, open item `done` and records an edit
  event of kind `closed` in place of an edit of the status
  (`service.close_without_finishing`). There are two ways to it, with the
  same refusals -- an item still waiting for confirmation, a finished one,
  one already closed: `tools.close_action_item` (an L2 action, run only
  after a person approves), and `POST /action-items/{id}/close`, which
  "끝내지 않고 닫기" in the detail window of an open item calls (#1077), on
  the meeting's list and on the team board. The window asks for no
  confirmation (the user, 2026-10-09): it says afterwards that the item was
  closed without being finished, and changing the status re-opens it, as it
  does a finished item. A closed item is in none
  of the counts B publishes for E's completion rate (`TeamActionProgress`,
  `service.team_action_progress`; asked by E's owner on #856) -- neither
  finished nor left undone, as a deleted item is. That event is all that
  tells a close from finished work -- `service.closed_unfinished`: the
  latest change of the item's status was a close -- and everything that says
  "finished" to or about a person reads it: the morning DM says "끝내지 않고
  닫힘" apart from 완료, the work-report draft has its own part, the
  assignee's calendar event is titled `[닫힘]` and not `[완료]`, the card in
  완료 is marked 닫힘 (`closed_unfinished` on the item read), the drawer's
  history says so, `workload_by_owner` does not count it as work its
  holder finished, and the agent's reads of an item (`meeting_action_items`,
  `action_item_status`) give its status as `closed`, not `done`, so Report
  and the chat do not call it finished. The event says that and when, about
  the item, and never who closed it; edit cost leaves it out, since a close
  corrects nothing the model wrote. An item re-opened and then finished is
  finished: the events are read in the order they were written (by id --
  on PostgreSQL an event's time is when its transaction began), and the
  close holds the item's row, so two closes at once leave one and the
  second is told the item is already closed. **Not told apart:** the Notion
  page and the Jira issue of a closed item read 완료 / a `done` status, and
  the minutes list it with the finished ones -- none of them has a closed
  state here
- Role-specific reports (Phase 2)

## AI stack

| Component | Model |
| --- | --- |
| Utterance classification | `kakaobank/kf-deberta-base` (DeBERTa, [MIT](https://huggingface.co/kakaobank/kf-deberta-base)), fine-tuned |
| Agreement verification | `klue/roberta-base` fine-tuned on KorNLI ([CC BY-SA 4.0](https://github.com/kakaobrain/kor-nlu-datasets) training data, server-only — #172) |
| Reference resolution, report generation | LLM |
| Due-date parsing | Rule-based Korean date parser (`slots.parse_due`). No model reads a date |

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
| Action item F1 | 0.43 (= 43%) — matching the best published AMI result, 43.12% (ADR 0006) | above it |
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

- A team's materials (`ext_materials`, #817) are a title and a Drive file id,
  by the decision on #817 to stop at the link and the preview: no text of a
  file is read, stored, embedded or sent to a model, no Drive permission is
  asked for, and no agent tool reads the table. The preview is Google's page
  under the viewer's own sign-in, so registering a file shows its title to the
  team and the file to nobody Google would not show it to. A row names no
  person -- not who registered it, not who opened it. The title is typed by a
  member and stored as typed, so it can hold a name; it and the file id stay
  out of logs and error messages, and neither goes to any outside service.
  The rows are not an analysis result and have no retention window: a member
  deletes one at any time, and they go with the team.
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
- S28's 동기화 기록 (`GET /sync-log`) gathers those same rows for a team and
  records nothing of its own. It follows the rule above and does not widen
  it: a Notion or Jira row goes to any member of the team; a failed
  calendar copy goes through the same `sync_state.failures_for` a card
  uses, so only to the item's assignee; and an event that was made is
  listed only for the person whose calendar holds it
  (`ext_calendar_events.user_id` -- the assignee, or, between a
  reassignment and the next copy, the person the item was assigned to
  before). A row carries the item's text as the board shows it, its
  meeting's title, the system, the kind or the link, and a time -- no
  assignee and no service message. The window draws an address as a link
  only when it is https.
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
