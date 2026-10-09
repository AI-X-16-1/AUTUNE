# Live research — design

Owner: 김민경 (@mkkim68). Status: draft for the owner's review, 2026-10-09.
Builds on: `2026-09-30-research-subagent-design.md` (Research after the meeting).

## 1. What this is for

During a live meeting, someone says something nobody can confirm on the spot —
"what did we decide about the pricing last month?", "how much does that API
cost?". Today Research only wakes on `intelligence.completed`, minutes after the
recording has been uploaded and analysed, and its document waits on 승인 대기.
Nobody sees it during the meeting, and nobody is told when it arrives.

Live research checks the live transcript as it is spoken, looks the question up
in the team's past meetings and on the web, and puts a short document beside the
live transcript within about 30 seconds. After the meeting, the documents stay on
the meeting page, and every participant who has linked Slack is sent a link.

### Decisions taken with the owner (2026-10-09)

| # | Question | Decision |
| --- | --- | --- |
| 1 | Sources | Both: the team's own meetings, and the web (Gemini grounding with Google Search) |
| 2 | What starts research | Both: automatic detection on the live rows, and a 조사 button on any live row |
| 3 | Detected question | Researched at once, no confirmation; at most 5 automatic per meeting |
| 4 | Who sees it during the meeting | The person running the live session, in a panel beside the live transcript |
| 5 | After the meeting | Kept on the meeting page (회의 중 조사), visible to the team |
| 6 | Slack | Sent automatically, no approval, to every participant with linked Slack — **a count and a link only**, never the document text (section 7) |
| 7 | How it runs | The browser relays the masked live rows to the agent layer (approach 1 of 3) |

Approaches not taken: module A publishing a Celery event per batch of live rows
(a contract change, and the live path is designed to touch no queue —
`docs/modules/audio-live-transcription.md`), and a separate agent WebSocket (more
than this needs).

## 2. Why the browser relays

The live path stores nothing. `LiveSession` masks each segment and sends it to
the browser as `{"type": "row", "utterance": …}` (`live/protocol.py`); the
`utt_live_…` ids never reach `utterances`. Nothing on the server holds the live
rows except the socket, and module A may not import `autune_agent`.

The browser already holds every masked row. It sends a window of them to a new
route of the agent layer. The agent layer treats that text like a chat message —
a team member's own input — and runs `assert_masked` on it again before any
model sees it. Nothing in module A, `packages/contracts` or `apps/` changes.

## 3. Flow

```
live socket ──row──▶ browser (LiveMeetingScreen)
                        │  every 6 new rows or 45 s ── POST /api/agent/live/{meeting_id}/detect
                        │  조사 on a row ───────────── POST /api/agent/live/{meeting_id}/research
                        ▼
                  agent router ──Celery──▶ autune.agent.live_research
                        │                     detect (Gemini) → search team meetings
                        │                     → web (Gemini + google_search) → write → save
                        ▼
browser polls GET /api/agent/live/{meeting_id}/documents every 5 s while live
```

### 3.1 Detect (automatic)

- **Window:** the browser keeps the rows since its last call and sends them —
  up to 12 rows, each `{start, text}` and **no speaker label** (a label may be a
  name). It sends when 6 new rows have arrived or 45 s have passed with at least
  one new row.
- **Route:** `POST …/detect` answers `202` at once and queues the task. It
  answers `409` while a detect for the meeting is still running (one at a time
  per meeting), and `429` once the meeting has 5 automatic documents.
- **Model call:** one Gemini call. Instructions ask for JSON
  `{"questions": [{"q": "<one sentence>", "web": true|false}]}`, at most 2, only
  for a question or a disputed fact that the speakers could not settle in the
  window. The questions already researched for the meeting are sent with the
  window, so the model does not ask the same one twice. A question that matches
  an earlier one after whitespace and case folding is dropped on our side too.
- **Budget:** a request is the instructions plus the window plus earlier
  questions, cut to fit `MAX_OUTBOUND_CHARS` (4000); the oldest rows go first.

### 3.2 Research (on 조사)

- `POST …/research` with `{row: {start, text}, context: [≤4 rows before it]}`.
  The row's text is the question as spoken; the model turns it into one
  sentence in the same call that writes the document.
- No per-meeting cap beyond a guard of 20 manual documents per meeting.

### 3.3 Research steps (both entry points)

Each question becomes one Celery task, `autune.agent.live_research`, on the
default queue:

1. **Terms:** reuse `writer.terms` (Research's term extraction).
2. **Team meetings:** `audio.search_team_meetings` per term, the current meeting
   excluded, through a `Toolbox` scoped to the team and the asker. At most 5
   quotes, titles stripped of speaker names as Research does (`_meeting_part`).
3. **Web:** when the detector said `web: true`, or always for 조사: one Gemini
   call with the `google_search` tool, the question only (no meeting text). The
   reply's grounding metadata gives the source titles and URLs; at most 3 are
   kept.
4. **Write:** one Gemini call writes Korean: a one-line title, 3–5 lines of
   summary, and the sources (past meetings by date and title, web pages by
   title and URL). It says plainly when nothing was found.
5. **Save:** `assert_masked(body)` then one `agent_live_research` row.

A failure in step 2 or 3 leaves that source out; a failure in step 4 saves a row
with `status = "failed"`, so the panel shows 조사하지 못했습니다 instead of
waiting forever. Errors are logged by type only.

## 4. Storage

New table `agent_live_research` (agent layer, `agent_` prefix, agent Alembic
branch):

| Column | Type | Note |
| --- | --- | --- |
| `id` | `String(64)` PK | `alr_…` |
| `team_id` | FK `teams.id` ON DELETE CASCADE | from the meeting |
| `meeting_id` | FK `meetings.id` ON DELETE CASCADE | retention and meeting deletion reach it |
| `requested_by` | FK `users.id` ON DELETE SET NULL | the person running the session |
| `origin` | `auto` \| `manual` | |
| `status` | `running` \| `done` \| `failed` | |
| `question` | text | masked; the dedupe key |
| `body` | text, nullable | masked; null until done |
| `web_sources` | JSONB | `[{title, url}]`, at most 3 |
| `meeting_sources` | JSONB | `[{meeting_id, title}]`, at most 5 |
| `created_at`, `updated_at` | | |

`agent_runs` rule 8 is unchanged: this table is a document store like
`agent_research_documents`, not a run log, and no run row keeps its text.

**Deletion.**
- Meeting deleted or expired: the FK cascade removes every document.
- A quoted past meeting deleted: `meeting_sources` is for display only. Deletion
  runs off a second table, `agent_live_research_sources(document_id,
  meeting_id)`, with the `agent_research_sources` trigger pattern, so a
  document goes with any meeting it quotes.
- A person deletes their speech (`DELETE /api/audio/me/speech`): the live text
  that fed a document is not tied to utterance ids, so the documents of every
  meeting the person took part in are deleted. This needs the
  `on_speech_deleted` receiver that #1014 asks for; live research ships with it.

## 5. API (agent router, `/api/agent/live/{meeting_id}`)

All routes need a signed-in current member of the meeting's team.

| Route | Body | Answer |
| --- | --- | --- |
| `POST /detect` | `{rows: [{start, text}]}` (≤12) | `202 {queued: true}`, `429` |
| `POST /research` | `{row: {start, text}, context: [...]}` | `202 {id}`, `409` (question already researched, or the meeting has 20 manual documents) |
| `GET /documents` | — | `[{id, origin, status, question, body, web_sources, meeting_sources, created_at}]`, newest first |

`assert_masked` runs on every incoming text before it is queued; a hit answers
`422 privacy` and nothing is queued. The Celery payload carries the row id of
the `running` document and the window text — masked meeting text, which the
pipeline's own events already carry for B and C. No audio, no file path.

## 6. Frontend

- **Live screen** (`features/transcript`, `LiveMeetingScreen`): a 조사 button on
  each live row (hover on desktop, a trailing icon on narrow screens); a 회의 중
  조사 panel beside the transcript that polls `GET /documents` every 5 s while
  the session is live. A new document slides in at the top with a short
  highlight; a running one shows 조사 중….
- **Meeting page:** the same list under 회의 중 조사, read once on load.
- The panel lives in `apps/web/src/features/transcript/`, following
  `ResearchCard`: `components/LiveResearchPanel.tsx`,
  `hooks/useLiveResearch.ts`, and the client calls in
  `features/transcript/api.ts`. The live screen mounts it.

## 7. After the meeting: Slack

`transcript.ready` arrives once the recording has been uploaded and the
participants are known. A new trigger handler in the agent layer (alongside
`on_event`, not a subagent) does:

1. If the meeting has at least one `done` live document, find the meeting's
   participants with a `user_id`, and their Slack member ids (#255).
2. Send each one DM: `회의 중 조사 문서 N건이 준비됐습니다. {web_base_url}/meetings/{id}#live-research`.
3. Record the send in `agent_live_research_notices(meeting_id, sent_at)` so a
   re-published `transcript.ready` does not send twice.

**A count and a link, never the text.** A Slack message cannot be recalled, and
the document holds meeting words a person may later delete (invariant 11, the
same reason `main/notify.py` sends a count). This is automatic, with no
approval: the owner decided it, and it is the first agent-layer message that
goes out without plan mode. `agent-layer.md` gets a paragraph saying so and
why — the message carries nothing a person would approve.

A team without Slack, an unlinked participant, or a Slack error is logged by
type and skipped; only `PrivacyViolationError` raises.

## 8. Privacy summary

- Outbound: detect sends masked row text without speaker labels; the web call
  sends only the masked question; the writer sends the question, quotes without
  speaker names, and web snippets. All through `HttpClient` → `check_outbound`.
- Stored: masked text only, `assert_masked` before every write.
- No speaking time, no counts per person, nothing per speaker at all.
- Deleted with the meeting, with any quoted meeting, and with a person's speech
  (section 4).
- Slack: count and link only.

## 9. Testing

- Unit (SQLite, `FakeRouter`-style fakes for Gemini): detect parses and caps,
  dedupe, 429 (detect) and 409 (research), `assert_masked` refusal, research with each source failing,
  failed status, document routes' membership check, notice sent once, no text
  in the DM.
- Postgres: the source trigger deletes a document when a quoted meeting goes;
  the meeting cascade; the speech-deletion receiver.
- Web: panel polling, 조사 button posts the row without the speaker, a failed
  card, the meeting-page list.
- By hand on dev: one scripted live meeting with two planted questions (one
  about a past meeting, one for the web); both documents appear within 30 s and
  the participants get one DM each after the upload.

## 10. Risks

- **Grounding through our client is unproven.** `main/gemini.py` sends plain
  `generateContent` bodies; the `google_search` tool and its grounding metadata
  have not been called from this code. The first plan task proves it on the dev
  key; if it fails, live research ships with team meetings only and says so.
- **Latency.** Detect, terms, search, web and write are four model calls; the
  30 s target is a goal, measured in the dev check, not a guarantee.
- **Deadline.** 10/12. Order of work: storage + routes + task (team meetings
  only), panel, web source, Slack notice, speech-deletion receiver.

## 11. Out of scope

- Showing documents live to other viewers of the meeting page (no channel for
  them; decision 4).
- Sending document text to Slack (section 7).
- Using the uploaded transcript to redo live documents.
