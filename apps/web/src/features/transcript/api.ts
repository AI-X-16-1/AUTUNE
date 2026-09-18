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
 * The bearer token for this browser, until there is a sign-in.
 *
 * `/transcripts/{id}` takes `CurrentUser` and refuses a request without one
 * (#213). Screen S01 does not exist and the shared client sends no
 * `Authorization` header (#156, #189), so until those land this feature attaches
 * the header itself. Two sources, in order:
 *
 * 1. `localStorage["autune.token"]` — pasted in by hand, so a person can switch
 *    users without a rebuild.
 * 2. `NEXT_PUBLIC_AUTUNE_DEV_TOKEN` — inlined at build time from `.env.local`.
 *
 * Both come from `POST /api/audio/dev/token`, which exists only when
 * `AUTUNE_ENV=local`. Nothing here is the sign-in design: when #189 puts the
 * header in `@/shared/api/client`, this function is deleted and `getTranscript`
 * goes back to one argument.
 */
function authHeaders(): HeadersInit {
  let token: string | null = null;
  try {
    if (typeof window !== "undefined") token = window.localStorage.getItem(TOKEN_KEY);
  } catch {
    // Private mode or blocked storage. Fall through to the build-time value.
  }
  token ??= process.env.NEXT_PUBLIC_AUTUNE_DEV_TOKEN ?? null;
  return token ? { authorization: `Bearer ${token}` } : {};
}
