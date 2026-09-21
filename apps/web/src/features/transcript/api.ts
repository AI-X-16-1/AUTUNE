/** Calls to /api/audio. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

export { api };

// TODO(김민경): add the calls this feature needs, e.g.
//   export const getReport = (meetingId: string) =>
//     api.audio<GapReport>(`/reports/${meetingId}`);

import type { Utterance } from "./types";

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
  api.audio<Utterance[]>(`/transcripts/${meetingId}`, { headers: authHeaders() });

/** Where a developer's browser keeps its token. Set by hand; see environments.md. */
const TOKEN_KEY = "autune.token";

/**
 * The bearer token this browser holds, or null.
 *
 * Two sources, in order: `localStorage["autune.token"]`, pasted in by hand,
 * then `NEXT_PUBLIC_AUTUNE_DEV_TOKEN`, inlined at build time. Both come from
 * `POST /api/audio/dev/token` (local only). This is not the sign-in design
 * (#156, #189); when the shared client carries the header, this goes.
 */
export function getToken(): string | null {
  let token: string | null = null;
  try {
    if (typeof window !== "undefined") token = window.localStorage.getItem(TOKEN_KEY);
  } catch {
    // Private mode or blocked storage. Fall through to the build-time value.
  }
  return token ?? process.env.NEXT_PUBLIC_AUTUNE_DEV_TOKEN ?? null;
}

function authHeaders(): HeadersInit {
  const token = getToken();
  return token ? { authorization: `Bearer ${token}` } : {};
}

/**
 * The API's origin. Duplicates the shared client's default because that
 * constant is not exported and a socket URL cannot go through `request()`.
 * A one-line follow-up for `@/shared/api/client` (#189 territory).
 */
export function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

/** `ws://` or `wss://` for the live channel, from the same origin as the API. */
export function liveSocketUrl(meetingId: string): string {
  return `${apiBase().replace(/^http/, "ws")}/api/audio/live/${meetingId}`;
}

/**
 * "Everyone in this recording consented", on the word of a team member (#283).
 *
 * Until #283 lands the route does not exist, and the shared client turns
 * FastAPI's own 404 body into a TypeError (#308). The gate shows whatever
 * message it gets, so that one case is named here in the user's language
 * rather than as "Cannot read properties of undefined".
 */
export async function attestConsent(
  meetingId: string,
): Promise<{ meeting_id: string; attested: boolean }> {
  try {
    return await api.audio<{ meeting_id: string; attested: boolean }>(
      `/meetings/${meetingId}/consent`,
      { method: "POST", headers: authHeaders(), body: JSON.stringify({ attested: true }) },
    );
  } catch (caught) {
    if (caught instanceof TypeError) {
      throw new Error("동의 기록 API가 아직 없습니다 (#283). 녹음은 그대로 시작할 수 있습니다.");
    }
    throw caught;
  }
}

/**
 * The whole recording, once, when the meeting stops (#259).
 *
 * Plain `fetch`, not `request()`: that helper sets `content-type:
 * application/json` and a multipart body needs the browser to set its own
 * boundary. Errors come back in the same shape `request()` would raise.
 */
export async function uploadRecording(
  meetingId: string,
  blob: Blob,
): Promise<{ meeting_id: string; status: string }> {
  const form = new FormData();
  form.append("file", blob, "recording.webm");
  const response = await fetch(`${apiBase()}/api/audio/meetings/${meetingId}/recording`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      message = ((await response.json()) as { error?: { message?: string } }).error?.message ?? message;
    } catch {
      // Not JSON; keep the status text.
    }
    throw new Error(message);
  }
  return (await response.json()) as { meeting_id: string; status: string };
}
