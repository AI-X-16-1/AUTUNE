# Privacy — Implementation Rules

Meeting recordings are among the most sensitive data a company produces. These
are not policy aspirations; they are constraints on the code. A change that
weakens one of them is rejected in review regardless of what it enables.

Product-level rationale: `../product/prd.md` section 6.

---

## 1. Raw audio is never persisted

The uploaded recording exists only for the duration of transcription.

**Required:**
- Write the upload to a temp path scoped to the task.
- Delete it in a `finally` block, so it is removed on success, on exception, and
  on cancellation.
- Set `privacy.original_audio_deleted = true` in `TranscriptReady` only after
  the file is actually gone.

**Forbidden:**
- Persisting the recording to object storage, a mounted volume, or a database
  column — including "temporarily, for debugging".
- Logging the file path in a way that survives the task, or attaching the audio
  to an error report.
- Passing a path to raw audio in a Celery payload. If a second task needs the
  audio, it belongs in the same task.
- Keeping a copy for model retraining. Training data collection is a separate
  product decision with its own consent flow, and it does not exist yet.

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

**User-reported misses** delete the affected utterance immediately. There is no
review queue: report, delete, then improve the detector.

## 3. Speaking ratio is private to the speaker

Each participant may see their own share of a meeting. Nobody else may see it —
not teammates, not the meeting organizer, not team administrators, not us.

**Required:**
- Compute the ratio, deliver it to that person by Slack DM, and do not persist
  the per-person value.
- Any endpoint that could return a speaking ratio authorizes on
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

Module E's aggregate metrics — quality score, alignment heatmap, gap
distribution — are team-level and contain no per-person speech volume.

## 4. Retention and deletion

- Analysis results are retained **90 days** by default, adjustable per team.
- A scheduled sweep deletes expired results.
- A user can delete their own data at any time. **The scope of "their own data"
  is under review — see ADR 0007, decision 5**, which would keep action items,
  decisions and lineage derived from a person's speech after that person's
  utterances are deleted. Until that ADR is accepted or rejected, "their own
  data" includes everything derived from their speech.
- When a user leaves a team, their utterances and everything derived from them
  are deleted. **This rule is under review — see ADR 0007**, which argues the
  record belongs to the meeting rather than to its participants, and that
  leaving is an access change rather than a data change. Until that ADR is
  accepted or rejected, this line is what the code follows.

**Required of every module:**
- Every module-owned table is reachable from a `meeting_id` or a `user_id`.
- Each module registers a deletion hook in `autune_core`'s deletion registry.
  Rows reachable by `ON DELETE CASCADE` from `meetings` are covered
  automatically — embeddings and topic graphs included, since both are
  PostgreSQL rows. Anything kept outside the database — a cached artifact, a
  file on disk — is your responsibility.
- Deletion is real. No soft deletes, no tombstones holding content.

A test proving that your module's data is fully removed when a meeting is
deleted is part of shipping a table, not an extra.

## 5. Consent

- Participants are notified when recording starts.
- A non-consenting participant's speech can be excluded from analysis. Excluded
  utterances are not stored, not just hidden.

## 6. Third-party services

Anything leaving our infrastructure — LLM APIs, Slack, Notion, Jira, Google
Calendar, error tracking, analytics — carries masked text only, and only what
the feature needs.

- Never send a full transcript to an external service when the feature needs one
  utterance.
- Never send raw audio anywhere.
- Error tracking must scrub message bodies; assume anything in an exception
  string is published.

## 7. Review checklist

Reject a pull request that does any of the following:

- [ ] Writes audio to a durable path, or removes a deletion `finally` block
- [ ] Writes transcript text before masking, or adds an unmasked column or flag
- [ ] Logs transcript text at any level
- [ ] Puts transcript text in an exception message
- [ ] Returns another person's speaking ratio through any surface
- [ ] Stores per-person speaking ratios
- [ ] Adds a table with no path to deletion by `meeting_id` or `user_id`
- [ ] Uses a soft delete for content
- [ ] Sends more data to a third party than the feature requires

## 8. When a rule blocks you

Say so. Do not work around it. Every one of these constraints has a legal or
product reason behind it, and there is usually a design that satisfies both the
feature and the constraint. Weakening the constraint quietly is the one
unacceptable outcome.
