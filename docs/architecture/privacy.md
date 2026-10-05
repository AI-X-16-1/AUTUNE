# Privacy — Implementation Rules

Meeting recordings are among the most sensitive data a company produces. These
are not policy aspirations; they are constraints on the code. A change that
weakens one of them is rejected in review regardless of what it enables.

Product-level rationale: `../product/prd.md` section 6.

---

## 1. Raw audio is never persisted

The uploaded recording exists only for the duration of transcription.

**Required:**
- Write the upload to a temp path **owned by exactly one party at a time**.
  The upload request owns it until the task is queued and deletes it if that
  fails (`storage.handover`); the task owns it from the moment it starts and
  deletes it in a `finally` (`storage.adopt`). A recording with two owners is
  deleted twice; one with none is never deleted, and that is the durable copy
  this rule exists to prevent.
- Delete it in a `finally` block, so it is removed on success, on exception, and
  on cancellation.
- Hand the file across processes **by job id, not by path**: the endpoint names
  the file after the job (`{job_id}.upload`) and the worker derives the path
  from the id it was queued with (`storage.upload_path`). The two ends never
  exchange a path.
- Collect orphans. A task can be lost after the enqueue; a sweep compares
  every file in the temp directory against its job's status in the database
  and deletes the ones whose attempt is over or has been running longer than
  a job can (`service.sweep_orphans`). Never on mtime alone — that deletes a
  file a late task is about to adopt. It runs at the start of every
  transcription task **and** hourly on beat
  (`autune.audio.periodic.sweep_orphans`, #207): the first is the only trigger
  that fires with no beat process running, the second the only one that fires
  when uploads have stopped — which is when orphans are made.
- Run the API and the worker against **the same `AUTUNE_AUDIO_TEMP_DIR` on the
  same filesystem**. The handover is a file on disk and an id in a message; if
  the two processes do not see the same directory, the worker finds nothing to
  adopt and the recording the endpoint wrote has no owner at all — the durable
  copy this section exists to prevent. Splitting them across hosts is not a
  deployment option today, and making it one means replacing the handover, not
  changing a path.
- Keep the scratch directory **owner-only**. The recordings themselves are
  `0600` — `NamedTemporaryFile` creates them that way — so their contents are
  already unreadable by another account on the machine. The directory's mode
  decides something narrower and still worth keeping: whether that account can
  *list* it, and read off the job ids, the file sizes and the times. Not what
  was said in a meeting, but who uploaded one, when, and how long it ran.
  `storage._private_directory` creates it at `0700`; a directory that already
  exists keeps its mode and is reported once, because `AUTUNE_AUDIO_TEMP_DIR`
  may point at a directory this process does not own (#351).
- Set `privacy.original_audio_deleted = true` in `TranscriptReady` only after
  the file is actually gone.
- A live recording lives in the recording tab's memory until its upload
  succeeds, then the tab drops it. **The one browser copy allowed:** when that
  upload fails and the server does not have the recording, the person who
  recorded may save the file to their own device by pressing "파일로 저장"
  (`recordingFile.saveRecordingFile`); without the save, closing the tab loses
  the meeting. "Does not have" is checked, not assumed
  (`recordingFile.serverHasRecording`): after a 409, a gateway error or no
  answer, the tab reads the meeting, and only a meeting already past
  `recording` means the server took it -- then the tab drops its copy as it
  does after a success. A 409 alone does not: the API also answers 409 while
  the live session still holds the meeting, having received nothing. Only when the tab cannot confirm that does it keep offering
  the save. Nothing saves on its own. Autune cannot delete a saved file, which
  holds the other attendees' voices too, so the screen asks the person to
  delete it once it is uploaded.

**Forbidden:**
- Persisting the recording to object storage, a mounted volume, or a database
  column — including "temporarily, for debugging".
- Logging the file path in a way that survives the task, or attaching the audio
  to an error report.
- Passing a path to raw audio in a Celery payload. Celery writes task arguments
  to the broker and to its own failure output; a path there is a path in a
  store. If a second task needs the audio, it belongs in the same task.
- Keeping a copy for model retraining. Training data collection is a separate
  product decision with its own consent flow, and it does not exist yet.

The wording above is decision #275. The original text said "scoped to the
task", which did not describe the upload → worker handover at all: the API and
the worker are different processes, and a file scoped to the request is one the
worker never receives.

Downstream modules must fail loudly if `original_audio_deleted` is not `true` —
that flag being false means the pipeline is broken.

## 2. Transcript text is masked before storage

PII masking happens inside module A, between transcription and the first write.

**Detected categories (MVP):** phone numbers, email addresses, national ID
numbers (주민등록번호), bank account numbers, card numbers.

**Detection is doubled:** regular expressions plus NER. Recall matters more than
precision here — a wrongly masked word is an annoyance, a leaked ID number is an
incident. Target recall is 0.95+ for the MVP and 0.99+ at three months.

**Masking format:** preserve shape, remove content — `010-****-5678`,
`k***@example.com`.

**Required:**
- Masking runs before the first `INSERT` of any transcript text.
- `utterances.text` contains masked text only.
- The unmasked string is a local variable in one function and is never returned,
  logged, cached, put on a queue, or sent to an external service.

**Forbidden:**
- An "unmasked" column, table, or debug flag anywhere in the system.
- Logging transcript text at any level, including `DEBUG`. Log utterance IDs.
- Including transcript text in exception messages — an exception string ends up
  in error tracking, which is an external service.
- Sending unmasked text to Slack, Notion, Jira, or any LLM API.

**User-reported misses** are masked immediately where they are stored (S30,
#555). There is no review queue: report, mask, then improve the detector.
- The browser sends character offsets, never the text; the span is read from
  the row, so the unmasked string is never in a request.
- Nothing of a reported span survives — not the first character a detector hit
  keeps (`masking.hide_reported`). Exact repeats in the same meeting are masked
  in the same request unless the reporter opts out.
- Once the meeting has been announced, A publishes `TranscriptReady` again
  after the correction commits, so B, C and D rebuild from masked text — the
  reprocessing async-pipeline.md already requires consumers to handle.
- The log line carries the category and counts only.
- A reporter can also add the span's **shape** to the team's masking rules
  (`masking_rules.py`, table `aud_masking_rules`): character classes and
  separators only — `A-20391` is stored as `A-#####` — so the table holds
  nothing a value can be recovered from. Only shapes with both a digit and a
  Latin letter, and at least four classed characters, become rules, after a
  trailing Hangul particle or unit is dropped: a name's shape would mask every
  word of that length, and a number's shape (`####`, `####-##-##`, `##:##`)
  every year, price, date and time — which module B reads due dates from. A
  purely numeric ID therefore cannot become a rule. Both the stored and the
  live path apply the team's shapes after the built-in masker. Removing a rule
  stops masking it in later transcripts; what it already masked stays masked.
  **Any team member can remove a rule**, which weakens masking for the whole
  team; there is no admin role yet (#592). The removal is logged with who did
  it.
This replaces the earlier rule that a report deletes the whole utterance:
masking removes exactly what was reported and keeps the evidence an action
item quotes readable around it.

## 3. Speaking ratio is private to the speaker

Each participant may see their own share of a meeting. Nobody else may see it —
not teammates, not the meeting organizer, not team administrators, not us.

**Required:**
- Compute the ratio, deliver it to that person by Slack DM, and do not persist
  the per-person value.
- Any endpoint that returns or presents a speaking ratio authorizes on
  `requester_id == subject_id`, with no admin override.

**Forbidden:**
- A table storing per-participant speaking ratios in aggregate.
- Any API response, dashboard widget, report, export, or Slack message
  containing another person's speaking ratio.
- Including speaking ratios in `IntelligenceSnapshot` or any other contract.
- An "anonymized" distribution across a small meeting — in a four-person
  meeting, a distribution identifies everyone.

The reasoning is that a per-person speech-volume metric visible to a manager
turns the product into a surveillance tool. That is a product-defining
constraint, not a configurable option.

### The meeting record is not a speaking-ratio product

This section binds what Autune **computes, stores, presents or exports**. It
does not forbid the meeting record from saying who spoke.

A transcript carries `speaker_id`, `start` and `end` on every utterance, so a
per-person duration is arithmetic away for anyone who can read it. That is not
a loophole, it is what a meeting record is: the same payload already carries
the full text of everything each person said, which is strictly more revealing
than how long they spoke. A rule that permitted the content and forbade the
duration would be protecting the wrong thing. Speaker attribution is also
load-bearing across the product — module B keys a commitment on who made it,
and `TranscriptReady` publishes `speaker_id` to four modules.

So the line is drawn at the derived metric, not at the record:

- **Allowed:** a transcript, an utterance, or an event carrying
  `speaker_id` with timings, to anyone entitled to read that meeting.
- **Forbidden, exactly as above:** any place where Autune itself turns that
  into a per-person speech-volume number — a field, a column, a widget, a
  report, an export, a Slack message, or a contract — for anyone but the
  speaker.

**A consumer must not derive it either.** A module that reads transcripts must
not aggregate utterance durations per speaker for anyone but that speaker — the
Slack DM and `GET /me/speaking-ratio/{meeting_id}` required above are the only
sanctioned uses — and the module that could is the one that pins it: a test
asserting no per-speaker duration or utterance count leaves its read paths
(module E, #371). Until #6, `Participant.user_id` was null on every meeting the
product had produced, so this was impossible in practice rather than prevented;
identification removed that accident and the rule now needs the test.

Decided on #361.

Module E's aggregate metrics — quality score, alignment heatmap, gap
distribution — are team-level and contain no per-person speech volume.

**Stance is the same kind of data.** Who backed a decision and who raised a
concern about it is a per-person record of behaviour in a meeting; visible to a
manager, it answers "who pushed back", which is the same surveillance shape as
speaking ratio. So:

- No contract, table, endpoint, dashboard, report or export carries one person's
  stance on a decision — not by participant id, not by user id, not by name.
- Stance crosses a module boundary only as counts per role
  (`Decision.stance_by_role`, added in #232), and a role is included only when
  at least **three** identified people held it at the meeting **and its stance
  is not unanimous**. In a small team a role is a person. A role in which
  everyone supported, or everyone raised a concern, says what each person did,
  so it is left out: `supporting` and `concerns` are each below `identified`,
  and together at most `identified`. A count of zero is allowed (#232). The
  contract enforces all of this (`RoleStance`,
  `STANCE_MIN_IDENTIFIED_PER_ROLE`); do not re-derive a lower number or a
  looser rule downstream.
- A consumer that aggregates stance over several meetings leaves a cell empty
  when its sample is too small, rather than showing a number that identifies the
  few people behind it.

Three is the same number module E already requires before it delivers speaking
ratios for a meeting (`_MIN_SPEAKERS_FOR_RATIO`, #128). Decided on #168.

**Action-item counts are a team total, never a meeting's** (#605, #619). B's
`TeamActionProgress` carries confirmed, done and overdue counts per meeting
and never an assignee. In a meeting whose confirmed items are all one
person's, those counts are that person's completion record. So:

- A consumer shows them only as totals over `ACTION_PROGRESS_WINDOW`, never a
  count per meeting, and keeps them out of meeting reports, direct messages
  and prediction features.
- A total over fewer than **three** meetings with confirmed items is left
  empty: with one or two, the team total is still a meeting's -- often one
  person's -- record. Three, as for stance and speaking ratios above.
- Adding an assignee to the contract is a privacy violation, not an additive
  change.

## 4. Retention and deletion

- Analysis results are retained **90 days** by default, adjustable per team.
- A scheduled sweep deletes expired results: `autune.audio.periodic.expire_meetings`,
  hourly (#206). Module A deletes every meeting past `meetings.expires_at` and
  everything cascades from it. The window starts when the meeting is held —
  the first live hello or the upload sets `expires_at` to now plus the team's
  `retention_days` (`service.open_retention_window`); the value
  `create_meeting` writes for a meeting booked ahead is provisional. A
  scheduled meeting that has not been held yet is never expired. A module's `on_meeting_deleted` hook runs before the row goes,
  and a hook that raises keeps the meeting for the next run.
- A voice profile is biometric data and lives on the person, not the meeting,
  so identification survives one meeting's window. It does not survive all of
  them: the same sweep deletes a profile once no remaining meeting names its
  owner (#363). Someone who stops attending is forgotten one retention window
  after their last meeting.
- What "delete their own data" runs today (module A, `account.py`):
  `DELETE /api/audio/me/speech` removes every utterance attributed to the
  person and every vector of their voice, and keeps the account;
  `DELETE /api/audio/me` does that, runs every module's `on_user_deleted`
  hook, and deletes the `users` row. **A hook that raises stops the account
  deletion** and leaves the account in place to retry (#358): a module's
  per-person rows are found by `user_id`, and deleting the user first would
  leave them unreachable. Every hook must therefore be safe to run twice.
- A user can delete their own data at any time. **The scope of "their own data"
  is under review — see ADR 0007, decision 5**, which would keep action items,
  decisions and lineage derived from a person's speech after that person's
  utterances are deleted. Until that ADR is accepted or rejected, "their own
  data" includes everything derived from their speech.
  **For a person deleting their own speech, decided with the user
  (2026-10-01, #587):** the utterances go, and in what was derived from them
  their words go too while the team's work stays — an unconfirmed draft drawn
  from the speech is deleted; a confirmed item or decision whose text is the
  line itself reads "삭제된 발화에서 만든 항목" and loses its date phrase and
  original sentence; a model's summary or a person's own writing stays; B's
  copies in Notion, Jira and calendars follow. Every module that keeps what it
  derived from speech — D's statements, E's report text — clears its own copy
  on the same signal; a module that does not yet is a gap to close. Modules
  receive this through `autune_core.deletion.on_speech_deleted`, before the
  utterances are deleted (ADR 0007 decision 5, #92). Module A sends it on
  **both** paths that delete a person's speech — `DELETE /api/audio/me/speech`
  and account deletion, where it runs before the user hooks — with the ids
  read before anything is locked, and again for any utterance that appeared
  meanwhile, so every utterance deleted is one the modules were told about. A
  hook that raises stops the deletion, and so do utterances that are still
  appearing after three rounds (409, try again) (#628).
- When a user leaves a team, their utterances and everything derived from them
  are deleted. **This rule is under review — see ADR 0007**, which argues the
  record belongs to the meeting rather than to its participants, and that
  leaving is an access change rather than a data change. Until that ADR is
  accepted or rejected, this line is what the code follows.

**Required of every module:**
- Every module-owned table is reachable from a `meeting_id` or a `user_id`.
  The exception is a team-scoped row that holds no meeting content and is
  deleted with its team -- a weekly report, `intel_action_progress`'s
  snapshot time -- while any per-meeting rows under it cascade from
  `meetings` (#619 review).
- Each module registers a deletion hook in `autune_core`'s deletion registry.
  Rows reachable by `ON DELETE CASCADE` from `meetings` are covered
  automatically — embeddings and topic graphs included, since both are
  PostgreSQL rows. Anything kept outside the database — a cached artifact, a
  file on disk — is your responsibility.
- Deletion is real. No soft deletes, no tombstones holding content.

A test proving that your module's data is fully removed when a meeting is
deleted is part of shipping a table, not an extra.

**A pending team invitation** (#552) is the one place Autune holds the
address of somebody who has agreed to nothing yet. It is kept in
`aud_team_invitations` only -- no `users` row is made for an address that
has not signed up -- and it does not wait for the analysis window: the row
is deleted when the invitation is accepted, when it lapses (seven days; the
retention sweep, and the next invitation made for that team), when a new
invitation to the same address replaces it, when the team or the inviter's
account is deleted, and when the invited person deletes their own account.
The link's token is stored as a hash, and log lines about invitations carry
ids, never the address.

The inviter may have the link **mailed from their own Gmail** (#552), when
they ask and only through their own `gmail.send` grant -- Autune runs no mail
server and holds no shared sender. That hands the address and the link to
Google, as the inviter pasting it into their own mail would. The message names
the inviter and the team, never the invited address; it is built and sent
inside the request that made the invitation, so the token never enters a Celery
payload; and the answer says only whether Gmail took it, which does not depend
on whether the address has an account here. Until it is accepted an invitation changes nothing
about what the invited person, or the team, can read.

**Copies outside Autune** (decided with the user, 2026-10-01; #588). Retention
and deletion apply to what Autune holds. An item or decision a team sent to its
own Notion or Jira, through an integration the team connected, is the team's
record there and is not deleted with the meeting or the person. What Autune put
on a person's own calendar is removed: on account deletion at once, with that
person's own grant (it goes with the account), and on a meeting's expiry by a
queued job, so a slow calendar never holds up the sweep. Both are best effort:
an account deletion does not wait on Google, so if Google does not answer an
event can remain on that calendar, and Autune's record of it goes with the
account anyway.

## 5. Consent

- Participants are notified when recording starts.
- A non-consenting participant's speech can be excluded from analysis. Excluded
  utterances are not stored, not just hidden.
- **Agreement to the terms and to the privacy policy is recorded per person**:
  which of the two, which version, when (`user_consents`, written by
  `packages/core` behind `/api/auth/consents`; #715). A changed document is a
  new version, which nobody has agreed to yet. The server records and does
  not gate: the consent page holds a person, no API call is refused.
  - **Withdrawal.** Those two are what using the service rests on, so taking
    the agreement back is leaving: deleting the account deletes the record
    with it (`ON DELETE CASCADE`). There is no separate withdrawal, and
    nothing is kept behind as proof — whether evidence of consent should
    outlive the account is part of #92.
  - **It is consent to nothing else.** The table can only say "agreed", so a
    consent a person must be able to refuse and withdraw is not recorded in
    it, and a check constraint keeps it to the two documents. In particular
    it does not permit voice data: module A's `voice_profiles_enabled` stays
    off until a separate, refusable consent exists and A reads that record
    (#268, #92 Q4). Which record is the source of truth for it is A's to
    decide with the privacy owner.
  - **It is not the consent to a recording.** That is per meeting, about the
    people in the room, and module A keeps it (`aud_consent_attestations`,
    `participants.consented`). The two never stand in for each other.

## 6. Third-party services

Anything leaving our infrastructure — LLM APIs, Slack, Notion, Jira, Google
Calendar, error tracking, analytics — carries masked text only, and only what
the feature needs.

- Never send a full transcript to an external service when the feature needs one
  utterance.
- Never send raw audio anywhere.
- Error tracking must scrub message bodies; assume anything in an exception
  string is published.
- A cloud model is never the default, and in module B it has to be switched on
  twice (#392). B's classifier, resolver, meeting summary and step-4 NLI send
  text to a provider only when their implementation is set to `llm` (or
  `llm_checked`), and B's settings refuse to load that unless
  `AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392=true` is set as well. Each sends
  masked text of consenting speakers only, with the team's names replaced:
  the classifier every utterance in windows, the resolver a commitment and
  the lines around it, the summary the meeting in sections, and NLI only the
  utterances the classifier called ambiguous, with one fixed hypothesis. The flag checks nothing about the meeting or the key -- the code
  cannot tell a real meeting from a dummy one, or a paid key from a free one --
  it makes sending speech out something a deployment says deliberately. Until
  #392 is decided, only demo meetings go through a deployment that sets it.
  This is module B's alone: the agent's, C's and D's cloud switches are their
  owners' and have no second switch today.
- What was delivered can outlive its source, for different reasons per
  destination, which is why each carries only what it needs:
  - **Notion:** a page in a team's workspace belongs to that team once written.
    Deleting the item in Autune retitles its page to "삭제된 액션아이템"
    and then moves it to Notion's trash, where the team can restore it for
    30 days without the item's sentence in the title (#768). Retention and meeting deletion do not
    reach it. A decision that stops being confirmed does not keep its page:
    the page is retitled first and trashed second, so what the trash holds
    for those 30 days is not the statement (#669). One exception: when the
    team's decision property map names no title -- one removed after the
    page was made, say -- the page cannot be retitled, and it goes to the
    trash with the statement for those 30 days; a warning is logged, by id
    (#679). A second: a page a person had already archived when its retire
    came. Notion refuses to edit a page in its trash, so it stays there with
    the statement, and Autune forgets it (#691) -- if someone restores it in
    Notion it stays, with the statement, and Autune does not retire it.
    Notion's own page history is out of Autune's reach: a workspace on a plan
    that keeps it can still show the earlier title to someone who restores
    the page.
  - **Jira (#82):** an issue lives in the team's site. Deleting the item in
    Autune closes its issue with a note rather than deleting it, so the
    team's own comments and work on it stay. The issue carries the item's
    description, due date and assignee's Jira account only.
    One read brings content back the other way: a team's screen can list
    the open issues of the project it connected (key, title, status,
    assignee's display name, due date), read from Jira when a member asks
    and passed through. None of it is stored or logged, so there is nothing
    of the team's Jira for Autune to retain or delete. It is read with the
    team's connection -- the grant of the person who connected it -- so
    every member of the Autune team sees those titles, whether or not they
    have an account on the Jira site (decided with the user, 2026-10-02).
    An issue with a Jira security level is left out of that list: the level
    restricts it to some people on the site, and the grant it is read with
    would otherwise pass its title to everyone on the team.
  - **Slack, a due-date reminder:** a direct message to an action item's
    assignee -- their own linked account, through the bot of the team that
    held the meeting -- the day before its due date and once after it
    passes. To that person and nobody else: no channel, no manager, no count
    of what anybody has missed. It carries the item's description, its due
    date, the meeting's title and a link to the meeting's board; no
    utterance. A message already delivered stays in that person's Slack
    when the item or the meeting is deleted; Autune keeps only that a
    reminder of that kind went (`ext_due_reminders`), and that goes with
    the item. A reminder the outbound check refuses is not sent, is
    reported once, and keeps that same row so it is not tried again. Each
    person can turn their own reminders off, and only their own
    (`ext_due_reminder_optouts`, which goes with the account); the same
    switch stops Monday's DM of that person's own open items (#792), which
    carries the same things about each item and goes to nobody else either.
  - **Slack, the morning DM:** on a Tuesday-to-Friday morning in Korea, a
    direct message to a person about their own items on one team: what
    changed since the last one (items of theirs now done, items they newly
    hold -- made, given to them, or confirmed since) and today's work (late, due today, in progress; the rest as a
    count). It carries what a reminder carries about each item -- its
    description, a late item's due date, the meeting's title -- and a link to
    the board; no utterance, and nobody else's items. "What changed" is read
    from `ext_edit_events`, which holds that an item was edited, which fields
    and when: the message never says who made a change, and it counts
    nothing about a person -- it is a list of that person's own work sent to
    that person. Autune keeps only that the day's message went
    (`ext_daily_digests`), not its text. The reminder switch above stops it.
    A morning DM or a Monday DM the outbound check refuses is not sent, is
    reported once, and keeps that day's (or week's) row so it is not tried
    again every ten minutes.
  - **A person's own leave dates:** a person may set one range of days on
    which the morning DM and Monday's DM are not sent
    (`ext_notification_pauses`). When someone is away is theirs alone: only
    they can read or write it, no screen or route shows it to a teammate or
    an admin, nothing is derived from it, and it is deleted once its last
    day has passed. Due-date reminders do not read it.
  - **Out-of-office time, from a person's own calendar:** where a deployment
    turns it on (`AUTUNE_EXTRACTION_LEAVE_FROM_CALENDAR`, off by default), a
    person who connected Google Calendar is not sent the morning DM or
    Monday's DM while that calendar marks them out of office. This is the
    one read of a person's calendar that is not of Autune's own events, and
    it is narrowed at Google twice: out-of-office events only
    (`eventTypes=outOfOffice`), and their start and end only -- no title, no
    description, no attendee, no other event is requested or returned. It
    asks about the minute the message would go, uses the answer to hold that
    one message back, and **stores nothing**: no table, log line or metric
    says a person was away. A calendar that cannot be read is treated as
    not away. Turn it on only once what the deployment tells people about
    the calendar connection says so; the settings screen says it where it is
    on.
  - **Public holidays:** no morning DM or Monday DM goes on one. The days
    come from Google's public calendar of Korea's holidays, fetched at its
    public address with no credentials -- nobody's Google grant is used and
    the request carries nothing -- and kept in `ext_public_holidays`, dates
    of public record. A table in code (the `holidays` package) answers when
    the calendar has not been read for two weeks.
  - **A copy that failed (#680):** Autune keeps, per item and system, only
    the kind of the latest failure and its time (`ext_sync_failures`) --
    never the outside service's message or what was being sent. It goes
    when the next copy goes through, and with the item. A failed copy to a
    person's own calendar is shown only to that person.
  - **A person's own calendar (#435):** Autune *can* remove its events — they
    carry its tag, and `delete_event` exists. Deleting an item deletes its
    event first. A meeting deleted or expired by the retention sweep does not
    yet: its rows cascade in the database with no call to each person's
    calendar (a deletion hook is the follow-up). A deleted account has its
    events removed first, by B's user hook, with the person's own grant; then
    the grant is revoked at Google (#763), and the row goes with the account
    (`user_integrations`, `ON DELETE CASCADE`). Both are best effort: an
    unreachable Google leaves the events on the calendar and the grant listed
    under the person's third-party access, and the deletion goes on. Each event is only the item's
    description and date, with no attendees and nothing from the transcript.
  - **A person's Google grants themselves (#760 review):** a deleted
    account's refresh tokens are revoked at Google before its rows go, the
    calendar's, `gmail_send`'s and `drive`'s alike (`GOOGLE_SERVICES`,
    `revoke_google_grants`, #763) -- best effort, as above: when Google does
    not answer, Autune still holds no copy afterwards, so nothing can use the
    token, and the person sees Autune under their Google account's
    third-party access until they remove it there. Disconnecting in Autune
    revokes too, and a
    revoke can end the person's other grant from the same Google account,
    which is then shown as needing a reconnect. Each grant asks for its own
    scope only, and a token that comes back carrying another grant's scope
    is refused -- refused, not revoked: it is never stored, and it stays valid
    at Google until the person connects again or removes Autune's access
    there. A calendar connected before #760 may carry sign-in's scopes
    (`openid email profile`) through `include_granted_scopes`; nothing
    before #760 asked for `gmail.send`, so no stored grant carries both
    personal scopes. Reconnecting the calendar gives it a token with its own
    scope only.
  - **A person's Drive (#817):** a person may let Autune read the Drive files
    they pick for it -- `drive.file`, a grant of its own (`drive`). It can
    open only a file that person chose in Google's own file picker: not the
    rest of their Drive, not a file somebody only sent a link to, and nothing
    of anybody else's. Connecting stores the grant and reads nothing. What a
    read then does with a file -- shown to the person who asked, held in
    memory for that answer, never stored and never logged -- is the reading
    code's to keep and is written here with it; until that code exists the
    grant is used by nothing.

## 7. Review checklist

Reject a pull request that does any of the following:

- [ ] Writes audio to a durable path, or removes a deletion `finally` block
- [ ] Writes transcript text before masking, or adds an unmasked column or flag
- [ ] Logs transcript text at any level
- [ ] Puts transcript text in an exception message
- [ ] Returns another person's speaking ratio through any surface
- [ ] Stores per-person speaking ratios
- [ ] Records, returns or exports one person's stance on a decision, or reports
      stance for a role below three identified people, or reports a unanimous
      role
- [ ] Adds a table with no path to deletion by `meeting_id` or `user_id`
- [ ] Shows action-item counts per meeting, or a team total over fewer than
      three meetings
- [ ] Uses a soft delete for content
- [ ] Sends more data to a third party than the feature requires

## 8. When a rule blocks you

Say so. Do not work around it. Every one of these constraints has a legal or
product reason behind it, and there is usually a design that satisfies both the
feature and the constraint. Weakening the constraint quietly is the one
unacceptable outcome.
