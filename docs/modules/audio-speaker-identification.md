# Speaker identification — design

**Date:** 2026-09-23 · **Owner:** 김민경 · **Module:** A · **Status:** Built;
threshold evaluation pending (issue #6; branch `audio/speaker-identification`).
**Profile creation is off by default** (`AUTUNE_AUDIO_VOICE_PROFILES_ENABLED=false`)
pending #92's biometric-consent legal review — see §4 and §8.

A voice the pipeline separated becomes a person. The transcript screens show
`화자 2 · 후보 김민경 · 유사도 0.87`; one click fills `speaker_id` and teaches
the system that voice for the next meeting. Nobody's name is written into the
record by a similarity score alone.

This is the second half of #6. The first half — separating voices — has
shipped: whole-file pyannote on the stored path, and a per-utterance embedder
with in-session clustering on the live path (`audio-live-speakers.md`). Both
produce `화자 N`, and both leave `speaker_id` null because there has been
nothing to compare a voice against.

---

## 1. What is decided, and why

| Decision | Choice | Reason |
| --- | --- | --- |
| How a profile is created | **By confirming a real meeting's speaker**, not by reading sentences into a microphone | No new recording UI, no separate step a person has to remember, and the vector comes from the same room and microphone the meetings use. S04 (the enrolment modal) stays in the design for later; it is not needed to make identification work |
| Who confirms | **A team member, in the app** — S13/S15's "참석자 중에서 지정", which is drawn and inert today | It works without Slack, which is not connected; the facilitator is reading the transcript anyway. The endpoint records `confirmed_by`, so S16's confirmation DM can call the same endpoint later and be told apart from a facilitator's pick |
| What a match does | **Proposes a candidate. Never assigns.** | `UnidentifiedSpeaker.tsx` already states the rule: "a similarity score high enough to show is not high enough to write into the record, because the cost of being wrong is a commitment filed under somebody who never made it." S13 draws it as `후보 · 유사도 0.62`. A second threshold for auto-assignment can be added later, with an undo, as its own decision |
| Where the vector comes from | **The worker, inside the block that holds the audio**, right after diarization | The recording is deleted the moment transcription finishes (invariant 11). A vector not taken then can never be taken: at confirmation time there is no audio left |
| Where matching happens | **At read time, in the API** — not stored | A candidate written into a column is stale the moment somebody else confirms a profile. Recomputing from the stored vectors is one pgvector query and is always current. It also keeps the worker from needing a profile lookup |
| Whose voices may be candidates | **Only members of the meeting's team** | A candidate from another team leaks that a person attended that team's meeting. Enforced in the query and pinned by a test |
| One profile vector or many | **Many — one row per confirmation**, matched against their mean | A voice changes with the room, the microphone and the day. Replacing a single vector would let one bad day overwrite a good profile; the mean of several is what `live/speakers.py` already does within a session |
| Lifetime | **An observation dies with its meeting; a confirmed profile lives on the person** | `privacy.md` section 4 allows a table reachable by `user_id`. Cascading profiles with meetings would reset identification every retention window |
| Storing an unconfirmed voice | **Only when the meeting has a consent attestation** | An embedding is biometric data. Without an attestation nothing is stored and the transcript is unaffected |

## 2. Data — one table, two kinds of row

`aud_speaker_embeddings`:

| Column | Observation row | Profile row |
| --- | --- | --- |
| `meeting_id` | the meeting, `ON DELETE CASCADE` | null |
| `speaker_label` | `화자 2` | null |
| `user_id` | null | the person, `ON DELETE CASCADE` |
| `vector` | `vector(256)` | same |
| `model_version` | the embedder checkpoint | same |
| `source_meeting_id` | null | where it was confirmed, `ON DELETE SET NULL` |
| `source_speaker_label` | null | `화자 2` |
| `confirmed_by` / `confirmed_at` | null | who pressed the button, and when |

- `pgvector` is already in the infrastructure and module D uses it
  (`ctx_embeddings`); `Vector(256)` comes from `pgvector.sqlalchemy`.
- **Vectors are compared only within one `model_version`.** A different
  checkpoint puts a voice somewhere else in the space; `diarization.py`'s
  `model_version` docstring says the same thing for turns. Changing the model
  does not corrupt anything — old profiles simply stop being candidates, and
  one confirmation each rebuilds them.
- `source_meeting_id`/`source_speaker_label` exist so a mistake can be undone:
  confirming the same (meeting, label) again **replaces** the profile row that
  pair produced. Without it, one wrong pick stays in that person's profile for
  good and drags every later match.
- Deletion: observation rows go with the meeting by `ON DELETE CASCADE`, and
  profile rows go with the person the same way (`user_id` is a `users.id` FK
  with `ON DELETE CASCADE`, as `team_members.user_id` already is). The
  `autune_core.deletion` registry's `@on_user_deleted("audio")` hook
  (`forget_user_voice`) is the path for a deletion that does not remove the
  `users` row itself — `@on_user_deleted` fires on **account deletion**, not
  on leaving a team (`packages/core/src/autune_core/deletion.py`). It deletes
  **every** profile row the person has, not only the ones sourced from one
  team, and it also nulls that person's id out of `confirmed_by` on other
  people's profile rows and `attested_by` on `aud_consent_attestations`, so
  someone who left the product is not still named as who confirmed a
  stranger's profile or attested a meeting's consent. `privacy.md` section 4
  asks for a test that proves it, and one exists
  (`test_speaker_endpoints.py`). **Plainly: nothing in the repository calls
  `run_user_hooks` today (#358)**, so this hook — registered correctly — does
  not run in production; there is no account-deletion flow to call it yet.
  `DELETE /me/voice-profile` (below) is the only deletion path that actually
  fires today.

**Why one table rather than two.** The two rows share every constraint —
dimension, model version, the vector itself — and confirmation is then one
insert from a row that already exists, not a translation between two shapes.

## 3. Pipeline — taking the vector

In `tasks.process_recording`, inside `with adopt(...) as recording:` (the only
place the waveform exists), after `get_diarizer().diarize(...)`:

```
decode → transcribe → diarize ─┬─→ one embedding per speaker label
                               └─→ audio deleted (finally)
```

For each label in the turns:

1. take that speaker's longest turns until **10 seconds** of speech are
   collected, concatenated in time order;
2. skip the label entirely when it has **less than 3 seconds** — a vector from
   a scrap of speech is noise, and a noisy row would be offered as a candidate
   and could be confirmed into somebody's profile;
3. embed with `live/embedder.py`'s `Embedder` — the same model the live path
   uses, so a live vector and a stored vector are comparable;
4. write one observation row.

The whole step is skipped when the meeting has no consent attestation, and
when the embedder cannot load: it logs `speaker_embedding_unavailable` with the
exception type and the pipeline carries on. A transcript without candidates is
a transcript; a pipeline that fails because of an optional model is not.

Nothing in this step touches `Participant.user_id`, so `TranscriptReady` is
published exactly as it is today.

## 4. API

Three endpoints under `/api/audio`, plus one small addition.

### `GET /meetings/{meeting_id}/speakers`

One entry per speaker label in the meeting:

```json
[{ "speaker_label": "화자 2",
   "user_id": null,
   "candidate": { "user_id": "usr_…", "name": "김민경", "similarity": 0.87 } }]
```

`candidate` is computed per request: the label's observation vector against the
mean vector of each team member's profiles, same `model_version`, best match
above `identification_threshold`, `null` when there is none. Team membership is
part of the query, not a filter applied afterwards.

**No counts, no durations.** `UnidentifiedSpeaker.tsx` removed "발화 41건" for
the reason this endpoint must not reintroduce: in a four-person meeting a
per-speaker count is a per-person speech volume, which `privacy.md` section 3
forbids.

### `POST /meetings/{meeting_id}/speakers/{speaker_label}`

Body `{"user_id": "usr_…"}`. Any member of the meeting's team may call it.

1. `Participant.user_id = user_id` for that label — the transcript screens now
   show the name, and `transcript_payload` will carry `speaker_id` on any
   later read;
2. **only when `AUTUNE_AUDIO_VOICE_PROFILES_ENABLED=true`** (default `false`,
   pending #92's Q4 — is a voice embedding 생체인식정보 under 제23조, and does
   collecting it need its own separate consent?): the observation row for
   (meeting, label) is copied into a profile row for that user, with
   `confirmed_by` and `confirmed_at`, replacing whatever profile row that same
   (meeting, label) produced before;
3. **regardless of the setting**, a profile row already sourced from that
   (meeting, label) is deleted — a flag that limits *collecting new* profiles
   must never block undoing an old one, including one written before the
   setting was turned off;
4. no observation row (no consent, too little speech, embedder unavailable) →
   step 1 still happens. Assigning a person is useful even when no vector can
   be learned from it.

With the setting off, step 1 is the only thing this endpoint does today: the
person is assigned, no profile is written, and no candidate will ever be
offered for them until the setting is turned on and they are confirmed again.

### `DELETE /me/voice-profile`

Deletes every profile row for the caller. `privacy.md` section 4: "a user can
delete their own data at any time." Identification simply stops proposing them
until they confirm again.

### `GET /teams/{team_id}/members`

Id and display name of each member, for the picker. `GET /teams` exists and
returns only the teams themselves; the picker needs the people.

## 5. Screens

No new screen. `UnidentifiedSpeaker` — rendered by S15 (stored transcript) —
gains the candidate line:

```
화자 2 · 후보 김민경 · 유사도 0.87   [ 김민경 맞습니다 ]  [ 참석자 중에서 지정 ▾ ]
```

- With a candidate: one click confirms.
- Without: the dropdown lists the team's members.
- **"직접 입력" and "확인 DM 보내기" stay disabled**, with a title attribute
  saying why. The first needs a decision about speakers who have no account;
  the second needs Slack, which is not connected. Neither is in this scope.

**Not built on S13, unlike this design assumed.** No `Participant` row exists
for a meeting until `persist_transcript` runs, so `GET
/meetings/{id}/speakers` returns `[]` for the whole time a meeting is
`recording` or `analyzing` — there is nothing yet for `UnidentifiedSpeaker` to
render or for an assignment control to act on. The build found this and left
the prompt off `LiveTranscript` entirely rather than show one with nothing to
offer (`useSpeakers.ts`'s docstring says the same). Assignment and the
candidate are stored-transcript-only, S15; the candidate appears there once
the worker has written the observation vector.

## 6. Errors and edges

| Case | Behaviour |
| --- | --- |
| Embedder cannot load in the worker | Step skipped, one warning with the exception type, transcript unaffected |
| No consent attestation | No observation rows; no candidates; manual assignment still works |
| Speaker with under 3 s of speech | No observation row for that label |
| Person has no profile yet | No candidate. The first meeting is always manual |
| Same person on two labels (over-split) | Both may be assigned to them; each adds a profile vector, which improves the mean |
| Re-assigning a label | `Participant.user_id` is overwritten; the profile row from that (meeting, label), if any, is deleted regardless of the setting, and replaced with a new one only when `AUTUNE_AUDIO_VOICE_PROFILES_ENABLED=true` |
| Model version changed | Old profiles are not candidates. One confirmation each rebuilds them |
| Meeting deleted | Observation rows cascade; profiles survive with `source_meeting_id` set to null |
| User removed from the team | Nothing happens today. `forget_user_voice` would remove every profile of theirs (not team-scoped — see §2), but nothing calls it (#358) |

`identification_threshold` is a setting, provisionally **0.70** — higher than
the live tracker's 0.55 because that one asks "is this the same voice as a
moment ago" and this one asks "is this a particular person". An evaluation
sets the real number and goes in `HISTORY.md`, the same way #306's did.

## 7. Tests

| Layer | What |
| --- | --- |
| Unit | Candidate selection over vectors, no model and no database: threshold boundary, the mean of several profile vectors, a different `model_version` excluded, no candidate when there are no profiles |
| Integration | No attestation → no observation rows · `GET` returns the candidate and its similarity · `POST` fills `Participant.user_id` and copies the profile · re-assigning replaces that source's profile row · `DELETE` removes only the caller's profiles · **a profile in another team is never a candidate** · deleting the meeting leaves the profile and nulls `source_meeting_id` · the deletion hook removes profiles with the user |
| Privacy | `GET` carries no count or duration · no vector in any log line or exception message |
| Frontend | `pnpm --filter web lint` and `typecheck`; there is no test runner |

## 8. What this does not do

- **Create a voice profile, by default.** `AUTUNE_AUDIO_VOICE_PROFILES_ENABLED`
  defaults to `false` pending #92's Q4 (biometric-consent legal review); until
  it is answered, or until authentication exists to record a separate consent
  (#268), confirming a speaker assigns them (`Participant.user_id`) and stores
  no profile vector. §4 has the detail; `docs/engineering/environments.md` has
  the variable.
- **Tell B, C and D.** `TranscriptReady` has already gone out with
  `speaker_id = null` when somebody confirms, so module B's action items keep
  their `assignee_label`. Propagating a later identification needs either a new
  event (a contract change) or a re-read on B's side, and re-publishing is
  unsafe while #194 stands. **Opened as its own issue (#360)**; this design
  stops at the transcript screens.
- Enrol a voice from a recording made for that purpose (S04).
- Send the confirmation DM (S16). The endpoint is shaped for it.
- Assign a speaker who has no account ("직접 입력").
- Identify anybody during the live session.
- Auto-assign above any threshold.

## 9. Dependencies

| | |
| --- | --- |
| #306 / #328 | The embedder and the vector space this reuses. Merged |
| #190 | Consent is per meeting, not per person. This design gates on the meeting's attestation, which is what exists |
| #194 | Why a re-publish is not an option for telling B |
| #237 | Role and seniority on a speaker — separate, and not needed here |

## 10. What this feature found outside module A

Five issues came out of this feature's reviews, none of them module A's to
fix:

- **#355** — `packages/core`'s `User.memberships` relationship lacks
  `passive_deletes=True`, so `session.delete(user)` raises instead of letting
  the database's own `ON DELETE CASCADE` / `SET NULL` do the work an
  account-deletion flow would need.
- **#356** — the shared engine (`packages/core/src/autune_core/db.py`) does
  not set `hide_parameters`, so a `StatementError` on a failed write carries
  its bound parameters — for this feature, a 256-float voice vector, inside
  an error message. Module A guards its own write paths against propagating
  one; the general fix belongs in `packages/core`.
- **#357** — `Participant.speaker_label`'s docstring claims identification
  *renames* the label ("'Speaker 2' until identification succeeds, then the
  person's name"). It does not — `assign_speaker` only ever sets `user_id` —
  and code written to that docstring would silently unlink every stored
  observation, which is keyed on `speaker_label`.
- **#358** — nothing in the repository calls `run_user_hooks`, so every
  module's `@on_user_deleted` hook, including this feature's
  `forget_user_voice`, is dead code until an account-deletion flow exists.
  See §2.
- **#359** — `shared/api/client.ts` rejects on every `204` response, which is
  why `assignSpeaker` in `apps/web/src/features/transcript/api.ts` calls
  `fetch` directly instead of going through it.
