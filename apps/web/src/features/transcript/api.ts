/** Calls to /api/audio. This feature calls no other module's endpoints. */
import { api, authHeaders } from "@/shared/api/client";

export { api };

import type { MeetingDetail, TeamSummary, Utterance } from "./types";

/** A meeting's stored transcript, masked.
 *
 * `/transcripts/{meeting_id}`, which is what `docs/modules/audio.md` plans and
 * what B (`/results/{meeting_id}`) and D (`/links/{meeting_id}`) already do.
 * This called `/meetings/{id}/transcript`, a path no backend route had — the
 * two halves of one module had picked different shapes for the same call.
 *
 * **Not the live one.** Utterances are written in a single transaction when the
 * task finishes, so this returns nothing until the meeting is processed and
 * cannot show a recording as it happens. `audio.md` gives that to a live
 * channel that goes straight to the screen, "never through a contract or an
 * event", and that channel does not exist yet.
 *
 * `Utterance[]`, not `TranscriptReady`: that payload is the announcement A
 * publishes once, and a consumer is required to check
 * `privacy.original_audio_deleted` before touching it. A screen reading a
 * stored transcript is not that consumer. `Utterance` is still a contract type,
 * so no mirror is hand-written here.
 */
export const getTranscript = (meetingId: string) =>
  api.audio<Utterance[]>(`/transcripts/${meetingId}`);

/** A meeting's own row: title, status, and the two privacy flags. What S12 polls. */
export const getMeeting = (meetingId: string) =>
  api.audio<MeetingDetail>(`/meetings/${meetingId}`);

/** The teams this person may open a meeting for. Feeds `createMeeting`. */
export const listTeams = () =>
  api.audio<TeamSummary[]>("/teams");

/** Open a meeting before there is any audio for it (S06, the file-upload path). */
export const createMeeting = (body: { title: string; team_id: string }) =>
  api.audio<{ meeting_id: string; status: string }>("/meetings", {
    method: "POST",
    body: JSON.stringify(body),
  });

/**
 * A member's statement that everyone in the recording consented (#190, #283).
 *
 * Sent before the upload, on purpose: module B and C analyse only consented
 * utterances, and a transcript written before this call is one they analyse
 * nothing of. The body can only be `true`; there is no un-attest.
 */
export const attestConsent = (meetingId: string) =>
  api.audio<{ meeting_id: string; attested: boolean }>(
    `/meetings/${meetingId}/consent`,
    {
      method: "POST",
      body: JSON.stringify({ attested: true }),
    },
  );

/**
 * Hand a recording to the pipeline. 202: queued, not done.
 *
 * Not through `api.audio`. The shared client sets `content-type:
 * application/json` on every request and spreads caller headers *after* it,
 * so there is no way to send a multipart body through it without also sending
 * a wrong content type — the browser has to set the boundary itself. This is
 * the one call in the feature that goes to `fetch` directly, with the same
 * base URL and the same error shape; when `@/shared/api/client` can carry a
 * FormData body this goes back through it.
 */
export async function uploadRecording(meetingId: string, file: File) {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch(
    `${API_BASE}/api/audio/meetings/${meetingId}/recording`,
    {
      method: "POST",
      headers: authHeaders(),
      body: form,
    },
  );
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = (await response.json()) as { error?: { message?: string } };
      message = body.error?.message ?? message;
    } catch {
      // Non-JSON error body; the status text will have to do.
    }
    throw new Error(message);
  }
  return (await response.json()) as { meeting_id: string; status: string };
}

/** Mirrors `BASE` in `@/shared/api/client`, which is not exported. See `uploadRecording`. */
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
