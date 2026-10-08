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
  **"Their speech" is the lines whose speaker label is assigned to them.** A
  label nobody assigned, or one whose assignment was undone
  (`DELETE /meetings/{id}/speakers/{label}`), is attributable to no one, and
  deleting one's own speech does not reach it. Any member of the meeting's
  team may undo an assignment, including one that names somebody else — the
  same people who may make one — so a person's lines can stop being theirs
  without their own action. That is deliberate: a wrong assignment has to be
  correctable by whoever notices it, and the confirmation says what it undoes
  (#928). Undoing is for a wrong assignment; a voice that diarization split
  into two labels should have both assigned to the same person, which the
  speaker picker allows behind a confirmation (#912).
- When a user leaves a team, **their membership goes and nothing else does**
  (decided with the user, 2026-10-06, #552). They can no longer read the
  team's meetings or anything derived from them. Their utterances, the items
  assigned to them and the decisions they took part in stay with the team, and
  their name stays on what they said. **Their participant rows keep their
  `user_id`**, so deleting their own speech (`DELETE /api/audio/me/speech`,
  above) still reaches every line of theirs after they have left: leaving
  does not delete a person's words, and it does not take away their way to
  delete them. That deletion is not per team -- it removes their speech
  everywhere at once -- and somebody who has left can no longer open the
  meetings to look first; the screen says both before they leave.
  A member leaves only by their own act
  (`DELETE /api/audio/teams/{team_id}/members/me`), and the last member of a
  team cannot leave it: a team with nobody on it could be neither read nor
  deleted. What the last member can do is delete the team (the next rule).
  This is ADR 0007's ownership rule -- the record belongs to the meeting, and
  leaving is an access change rather than a data change -- **without that
  ADR's mechanism**, which clears `participants.user_id` on departure and
  would end the departed person's deletion with it. The ADR is still Proposed
  and its legal review has not happened (#92); what a "no" there would change
  is written in the ADR, under *Not taken yet*. Until 2026-10-06 this line
  read "their utterances and everything derived from them are deleted", and
  nothing did that: there was no way to leave a team.
- **The last member of a team may delete the team** (decided with the user
  and the four other owners, 2026-10-09, #1007; before the legal review of
  ADR 0007, #92 -- that ADR carries a dated note on it).
  `DELETE /api/audio/teams/{team_id}`, by the one person still on the team,
  with the team's name typed on the screen and sent in the request's body.
  A team with two or more members is as it was: nobody deletes it, and nobody
  takes anybody else off it.
  - **What goes.** The `teams` row and every row PostgreSQL reaches from it or
    from its meetings by `ON DELETE CASCADE`: the membership, the team's
    integrations with their tokens, pending invitations, masking rules, every
    meeting with its participants and utterances, and what modules A to E and
    the agent layer keep for the team or for its meetings. The utterances of
    people who left the team earlier go with the rest. They are not told: no
    new message leaves Autune for this. Until the team is deleted their own
    deletion reaches their lines as above.
  - **The order is the retention sweep's.** Every meeting's
    `on_meeting_deleted` hooks run before that meeting's row goes, and all of
    them before the `teams` row. The cascade does not run them, and they are
    what moves B's calendar events and C's agenda lines to the clean-up
    queues; those queues are keyed by person, so they outlive the team and are
    worked through with each person's own Google grant. A hook that raises
    stops the deletion and leaves the team and every meeting of it in place
    to ask again, as a user hook stops an account deletion; clean-up a hook
    had already queued still runs, which is why every hook is safe to repeat.
  - **Refused while a meeting of the team is being processed** (409
    `team_meeting_in_progress`): a transcription job that is queued or
    running -- a job is running until its `TranscriptReady` has gone out --
    or a live session that is open. Deleting under a queued job would leave
    its recording in the temp directory until the orphan sweep's age limit
    (section 1); deleting between the transcript's commit and its publish
    would announce a meeting that is gone; and a live socket checks
    membership once, at its hello. Also refused: when anybody else is on the
    team (409 `team_has_other_members`), and when the name sent is not the
    team's (422 `team_name_mismatch`).
  - **What stays is outside Autune**, and deleting the team ends Autune's way
    to reach it. The screen says so before the name is typed, and names the
    first three:
    - B's copies in the team's own tools: project minutes in Notion, Slack
      and Jira, and the Notion pages and Jira issues of items and decisions.
      A meeting's expiry takes the project minutes back through the team's
      integration; a team's deletion deletes that integration and the queue
      of copies still to take back (`ext_project_send_cleanup`) with it.
    - C's notices in the team's Slack channel (S20). C keeps no message id
      and takes none back at a meeting's expiry either.
    - E's meeting reports, their corrections and the weekly reports in the
      team's Slack channel, which E never takes back.
    - D's messages in the team's Slack channel: the topic-link notice, the
      decision-drift warning and the pre-meeting brief, which quotes the
      decisions of the meeting it recaps. D keeps no message id and takes
      none back at a meeting's expiry either.
    - What was sent to a person's own Slack DM -- a confirmation request, a
      reminder, a digest, a decision-drift warning, their own speaking
      ratio -- stays in that person's DM, as at a meeting's expiry.
    - A `TranscriptReady` message already published and not yet consumed
      stays in the broker until a consumer takes it. It holds the masked
      transcript, and nothing removes it earlier.
    - What was already sent to a model provider follows that provider's
      terms (section 6), as it does without any deletion.

    What Autune put on a person's own Google Calendar does not stay: the
    queued jobs above remove it.
  - **A voice profile is not deleted in the request.** It is the person's,
    not the team's. The hourly sweep deletes it once no remaining meeting
    names its owner, as it does after an expiry.

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
invitation to the same address replaces it, when a member of the team
cancels it, when the inviter leaves the team (somebody who is no longer on a
team brings nobody onto it, so their pending invitations to it go with
them), when the team or the inviter's account is deleted, and when the
invited person deletes their own account.
The link's token is stored as a hash, and log lines about invitations carry
ids, never the address.
While it is pending the address is shown to the members of that team, and
to nobody else, with when the link lapses and who invited
(`GET /api/audio/teams/{team_id}/invitations`): the list looks nobody up, so
it reads the same for an address with an account and one without, and it
never carries the token or its hash. Any member of the team may cancel one.

**An invitation may also be made for no address** (#552, 2026-10-06). It
holds nobody's address -- there is none to hold -- and for the same reason
nothing but the link says who may use it: **whoever has the link joins the
team by signing in.** That is the one place the read boundary of a team
rests on a link alone, so it is kept small: the link admits one person and
is then gone; it **lapses one hour** after it is made (seven days is for an
invitation with an address); a person has one open for a team at a time,
and making another ends the earlier one; it is never mailed by Autune; and
it is in the same pending list, as a link with no address, where any member
of the team can cancel it. Its token is stored as a hash like any other,
and log lines carry ids. Nobody is told when somebody joins by it -- the
member list shows them. **Somebody who joins by it stays for as long as
they choose to.** A member can leave a team by their own act (section 4,
the departure rule), and nobody can take another member off: removing
another member was never built, and otherwise a `team_members` row goes only
with the account or the team. So a link that reaches the wrong person admits
them, with everything the team can read, until they themselves leave -- the
team cannot put them out -- and the limits above are all there is against
it: they make it one person within one hour, they do not undo it. This is
accepted for now, knowingly; a way to remove a member is what would change
it. A link somebody made goes when they leave the team, like their other
pending invitations. An invitation for an address is unchanged and is
still only for that address.

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
account anyway. Module C's agenda lines on a person's own calendar follow
the same rule -- see "Google Calendar, S20's 다음 회의 잡기" in section 6.
When a team is deleted by its last member (section 4, #1007), the calendar
entries are removed the same way, and everything in the team's own tools
stays -- B's project minutes among them, which an expiry would have taken
back: the integration that could reach them is deleted with the team.

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
  - **Slack, an extraction that failed:** one message to the channel of
    the team that held the meeting, once, when B's extraction of a meeting
    has failed three times in a row (`ext_extraction_attempts`) -- or when
    the extraction was stored and its result could not be passed on to the
    other analyses three times in a row (#887), in which case the message
    says that and not that nothing was extracted. Either way it carries
    the meeting's title, the number of attempts and a link to the meeting's
    액션 tab; no utterance, no name that B adds, and not the error -- only
    the error's class is kept, in B's own table and log. The title is a
    value a person typed and is sent as it is stored, so it can hold a
    name: "no name" is true of what B puts in the message and not of the
    title. A title in which the outbound check finds personal data -- a
    phone number, an e-mail address and the like; it does not find names
    -- is refused, and then no message goes and it is not tried again. A
    team with no channel
    connected gets no message. A message already posted stays in the
    channel when the meeting is deleted.
  - **Slack, the morning DM:** on a Tuesday-to-Friday morning in Korea, a
    direct message to a person about their own items on one team: what
    changed since the last one (items of theirs now done, items of theirs
    closed without being finished, items they newly hold -- made, given to
    them, or confirmed since) and today's work (late;
    due today; standing untouched for five days or more, when the item is in
    progress or has no due date; in progress; the rest as a count). A
    standing item's line says how many days it has stood: the
    time since the item was made or last edited, read from the same edit
    record as "what changed" and worked out each morning, not stored. It is
    how long an item has waited, said to the person who holds it. It carries
    what a reminder carries about each item -- its
    description, a late item's due date, the meeting's title -- and a link to
    the board; no utterance, and nobody else's items. "What changed" is read
    from `ext_edit_events`, which holds that an item was edited, which fields
    and when: the message never says who made a change, and it counts
    nothing about a person -- it is a list of that person's own work sent to
    that person. **An item closed without being finished is not called
    done** (#856): a close ends in the same status as finished work, so it
    leaves an event of its own kind (`closed`) -- that the item was closed
    and when, and not who closed it -- and this message and the work-report
    draft below say "closed" from it, so that neither tells a person they
    finished what was closed, by them or by somebody else. Autune keeps only
    that the day's message went
    (`ext_daily_digests`), not its text. The reminder switch above stops it.
    A morning DM or a Monday DM the outbound check refuses is not sent, is
    reported once, and keeps that day's (or week's) row so it is not tried
    again every ten minutes.
  - **Slack, the work-report draft:** on a Monday-to-Friday afternoon
    in Korea (16:00-17:00, and not later), a direct message to a person about their own items on one
    team, when something of theirs was finished or moved to in progress that
    day: a short report -- finished, closed without being finished, moved,
    going on to tomorrow, late, and the rest as a count (a close alone is not
    a day's work and sends none) -- headed by the team's name and worded so
    that the
    person can paste it to that team. **Autune sends it to that person and to
    nobody else**: no channel, no lead, no admin, and no collected version of
    several people's days; whether anybody else reads it is the person's own
    paste. It carries what the morning DM carries about each item -- its
    description, a due date that is today's or past, the meeting's title --
    and a link to the board; no utterance, and nobody else's items. "Today"
    is read from `ext_edit_events` as the morning DM's "what changed" is, so
    it never says who made a change, and it counts nothing about a person
    beyond the number of their own open items it did not list. The text is
    made from the rows; no model reads or rewrites it. **That it went is
    kept for its own day and no longer** (mkkim68, review of #954). Unlike
    the morning DM, this one goes only on a day the person finished or
    started something, so the row that says it went (`ext_work_reports`:
    person, team, day) says by itself that they worked on that team that
    day; kept, the rows would be a calendar of a person's working days --
    the per-person record of conduct ADR 0003 forbids. The row has one use,
    not sending twice in a day, so the sending task deletes every earlier
    day's row each time it runs: every ten minutes, also where the feature
    is switched off and outside its hour. A row is therefore gone within
    about ten minutes of the next midnight in Korea while the worker runs,
    and at the worker's first run if it was down; until then it goes with
    the account or the team. For the same reason nothing else names a
    person beside a day: the task's result is a count, a failed send is
    logged by team and error type, and a refused text is raised by team.
    The run's log line keeps how many went, not to whom. Not its text
    either. The reminder switch above stops it, and so do the person's own
    leave dates and a public holiday below; a draft the outbound check
    refuses is not sent, is reported once, and keeps that day's row -- which
    is deleted with the others.
  - **A person's own leave dates:** a person may set one range of days on
    which the morning DM and Monday's DM are not sent
    (`ext_notification_pauses`). When someone is away is theirs alone: only
    they can read or write it, no screen or route shows it to a teammate or
    an admin, nothing is derived from it, and it is deleted once its last
    day has passed. Due-date reminders do not read it.
  - **Those dates on the person's own calendar, by their own tick** (the
    user, 2026-10-06): the one way the dates leave Autune. A person whose own
    Google Calendar is connected is shown a box beside the dates, "내 Google
    캘린더에도 추가", **off until they tick it**; nobody else can tick it for
    them and no setting of a team or a deployment does. Ticked and saved, the
    range goes onto that person's own calendar through their own grant as one
    all-day event: the two dates, the fixed title "휴가" and a fixed line
    saying where it came from -- no meeting, no item, no other person, no
    attendee, so nobody is invited or notified. **Who can see an event on a
    calendar is decided by that calendar's sharing, not by Autune**, so it is
    written `visibility: private`: someone the person shares the calendar
    with sees that they are busy on those days and not why. That is still
    more than "theirs alone", and it is why it happens only on the person's
    own press and is said before they press -- under the box, and beside the
    calendar's connect button, to somebody already connected as well.
    Autune keeps the event's id on the pause row
    (`ext_notification_pauses.calendar_event_id`) and, only while a save is at
    the calendar, the time that save began (`calendar_claimed_at`, cleared
    when it returns; it keeps a second save out and is shown nowhere) -- and
    nothing else: a changed range moves the
    same event, and a save with the box unticked, or clearing the dates,
    removes it (a removal Google does not answer is queued and tried again
    with the person's grant, as a due-date event's is). A save that says
    neither -- the box was not drawn, the calendar not being connected just
    then -- leaves the event and its id as they stand, and makes no event
    where there is none; an event the person deleted in Calendar is not
    made again by such a save either -- the id is dropped, and only a tick
    makes one. Once the last day has passed
    the row is deleted as before, the id with it, and **the event stays** on
    the calendar as the person's own record; Autune can no longer reach it.
    A calendar disconnected while the event stands cannot be reached either:
    the event stays there, where the person can delete it, and the save that
    would have removed it tells them so.
    A deleted account has an event that still stands removed first, by B's
    user hook. Autune never reads the calendar for any of this: a leave the
    person wrote there themselves is not looked for, and the out-of-office
    read below asks Google for out-of-office events only, which this plain
    event is not. No log line carries the dates. Like an item's due date
    (#435) and a project's minutes a person chose to send (#788,
    `ext_minutes_events`), it goes onto the writer's own calendar and nobody
    else's. Unlike a due date, which follows from the connection, it is
    about the person and not the team's work, so connecting a calendar is
    never enough: a range goes only when the box is ticked at that save.
    Still true of everything inside Autune: no screen, route or message
    shows one person's dates to another.
  - **Out-of-office time, from a person's own calendar:** where a deployment
    turns it on (`AUTUNE_EXTRACTION_LEAVE_FROM_CALENDAR`, off by default), a
    person who connected Google Calendar is not sent the morning DM or
    Monday's DM while that calendar marks them out of office. This is one of
    two reads of a person's calendar that are not of Autune's own events
    (the other is C's event picker, "Google Calendar, S20's 다음 회의 잡기"
    below), and it is narrowed at Google twice: out-of-office events only
    (`eventTypes=outOfOffice`), and their start and end only -- no title, no
    description, no attendee, no other event is requested or returned. It
    asks about the minute the message would go, uses the answer to hold that
    one message back, and **stores nothing**: no table, log line or metric
    says a person was away. What is kept is what any digest that goes
    leaves -- that it went and when (`sent_at` on `ext_daily_digests` and
    `ext_weekly_digests`, and the sending task's result, which names who it
    went to). A digest that went later than usual went late for one of
    several reasons -- a worker that was down, a Slack account linked that
    morning, an item assigned at eleven, a send that failed and was tried
    again, or the person being back -- so a late time leaves room to guess
    at the reason; the reason itself is recorded nowhere (mkkim68, review of
    #841). A calendar that cannot be read is treated as
    not away. Turn it on only once what the deployment tells people about
    the calendar connection says so; the settings screen says it where it is
    on.
  - **Slack, the notice after a meeting:** soon after a meeting is
    processed, a direct message to a person the pipeline has put work of that
    meeting on, on the meeting's team now. It carries the meeting's title --
    a value a person typed, sent as stored, as the reminders carry it -- **how
    many** drafts wait for that person's confirmation, and a link to the
    meeting's 액션 tab. It carries nothing of a draft: not its text, not its
    date. A draft is a model's guess until a person confirms it, and
    unconfirmed content does not reach an outbound surface (#246; the agent
    layer's rule 3). An item of theirs that a person has already confirmed is
    named with its date, as in the morning DM. The count is of items waiting,
    said to the person they wait for; nothing is counted about a person and no
    utterance is read. Autune keeps only that the notice was sent or
    refused (`ext_meeting_notices`: the meeting, the person, when), once a
    person and meeting; the row does not say which of the two, and it goes
    with the meeting -- when it is deleted, its retention expiry included --
    and with the person's account. The message itself stays in the person's
    Slack. 09:00-17:00 Korea time on a working day, and not on a public
    holiday; the reminder switch, a person's own leave dates and, where
    that read is on, an out-of-office event on their calendar stop it. A
    notice the outbound check refuses is not sent, is reported once, and is
    not tried again.
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
    under the person's third-party access, and the deletion goes on. Each of those events is only the item's
    description and date, with no attendees and nothing from the transcript.
    B writes two other kinds of event on a person's own calendar, each only
    by that person's own act and each removed by the same user hook: a
    project's minutes they chose to send (#788, `ext_minutes_events`) and
    their own leave dates ("Those dates on the person's own calendar",
    above).
  - **Google Calendar, S20's 다음 회의 잡기 (module C, #824):** the presser's
    own calendar only, with their own grant (`user_integrations`). A line
    carries a gap's title and suggested question -- both stored masked, or
    for a question a member rewrote, pattern-checked (see the note on edited
    questions below) -- and the gap's id; never an utterance, a score, or anything from the
    participation matrix (section 3). Settled with mkkim68 on #824:
    - *Nobody else's calendar.* S20's "담당자 지정해 질문" does not write to the
      teammate's calendar: one person's grant is for their own work only
      (#435's rule), and a question picked by somebody else is not. It is a
      mention on the team channel instead -- see "Slack, S20's team notices"
      below.
    - *The picker reads four fields.* So that the presser can pick the next
      meeting, C lists the timed, uncancelled events on their own calendar
      for the next 14 days, asking Google for each event's id, title, start,
      end and status only (`fields`), and returns them to that person only
      (`GET /api/gap/agenda/{meeting_id}/events`). Unlike the out-of-office
      read above, this one returns event titles, and a title can name other
      people. Nothing of it is stored; the log line holds the count. Without a
      pick, C looks only at events starting within five minutes of the team's
      next scheduled meeting, to find that meeting's event.
    - *The write reads the description and the guest list.* C asks the picked
      event for its description and its attendees' addresses only, adds one
      line per gap (`[Autune 갭] <title> — <question> (<gap id>)`), and writes
      the description back. The existing description passes through Autune's
      server for that request and goes through the outbound check with the
      rest of the body, so an event whose description already holds what the
      check refuses is not written. Neither the description nor the addresses
      are stored or logged.
    - *The people already invited are told.* Adding lines asks Google to send
      its change notice (`sendUpdates=all`) to the event's attendees, so the
      meeting's members see the agenda in their own calendars. Nobody is
      invited, and by the refusal below everyone notified is on the team.
      Taking lines out sends nothing.
    - *Not onto an event shared outside the team.* Google shows a description
      to everyone on the event, so an event with an attendee who is not on the
      meeting's team is refused (`external_attendees`) and the screen says
      why. Meeting rooms and the presser do not count. An event whose guest
      list Google does not return whole -- the organizer hid it from guests
      and the presser is not the organizer, or Google says attendees were
      omitted -- cannot be checked, so it is refused too
      (`hidden_attendees`; mminjae97 on #872). A guest invited after the
      line was written sees it until it is taken out.
    - *Every line is recorded, and comes out again.* `gap_agenda_events` keeps
      the meeting, the gap, whose calendar and which event -- the calendar's
      owner is also who pressed, kept because the line can only be removed
      with their grant, as B keeps an item's assignee; no screen, route or
      tool reads it. Taking a gap back removes its line and its record. A
      meeting deleted or expired has its records copied to
      `gap_agenda_cleanup` by C's meeting hook, and the worker takes the lines
      out with each owner's grant (`drain_agenda_cleanup`, every ten minutes,
      five tries). A deleted account has its lines taken out at once by C's
      user hook, before the grant goes. A person deleting their own speech
      (#587) resets a question that named their words, and that gap's lines
      are queued to come out the same way. All best effort, as section 4
      says: a refused grant, an unreachable Google or a description the
      outbound check refuses leaves the line on the calendar, logged.
  - **Slack, S20's team notices (module C, #824):** two messages to the
    channel of the team that held the meeting, each once per press, with the
    team's connection. "담당자 지정해 질문" posts one gap's title and suggested
    question, mentioning the member the presser picked from the meeting's
    team -- by the Slack account that member linked themselves, or by their
    display name when they linked none -- and the presser's display name.
    "다음 회의 잡기" posts, once the calendar took them, the titles and
    questions of the gaps whose line is new on the event, the meeting's
    title and the presser's display name; pressing again posts nothing.
    Titles and C's own questions are stored masked, and a question a
    member rewrote is pattern-checked (below); every value is escaped so it
    cannot become a mention or a link, and no utterance, score or
    participation figure is sent. Every value is one Autune stored, so a
    message the outbound check refuses is not sent and is logged as an
    error with the meeting's and gaps' ids (`gap_slack_refused`), apart
    from Slack not answering, and the screen says it was not sent; it is
    not raised, because "다음 회의 잡기" has already written the calendar
    by then. Mentioning a member reads the Slack account they linked
    (`user_integrations`), which counts as acting for that member under
    `data-model.md`'s rule: it is read only to tell them. The card does
    show the channel whether the member linked one -- a mention or a plain
    name -- which mkkim68 accepted on #824; the screen is not told. Nothing
    about either message is stored; the picker returns names and ids only,
    never whether a member linked Slack or a calendar. A team with no channel connected gets no message
    and the screen says so. A message already posted stays in the channel
    when the meeting is deleted.
  - **A 해소용 질문 a member rewrote (module C, #824):** S20's "편집" stores
    text a person typed, and it goes where C's own question goes -- the
    team's Slack channel, the next meeting's event, E's report. It is
    checked before it is stored, not masked: `find_unmasked` refuses text
    holding a pattern it knows (a phone number, an address, an id number)
    with a 422 that names no value, and nothing changes. The check is
    pattern-based, so a name a person writes, or someone's words a person
    copies in, passes it -- the same standing as a meeting's title (#889)
    and B's hand-edited items, and reaching only the team's own channel,
    events whose guests are all on the team, and E. Deleting speech resets
    an edited question only when it names a topic label that is gone
    (#587); words copied in by hand stay, as anything a person wrote does.
    Whether hand-written text should follow another rule is open with
    mkkim68 for B and C alike (#872 review).
  - **A person's Google grants themselves (#760 review):** a deleted
    account's refresh tokens are revoked at Google before its rows go, the
    calendar's and `gmail_send`'s alike (`GOOGLE_SERVICES`,
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
