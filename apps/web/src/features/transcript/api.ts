/** Calls to /api/audio. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

export { api };

// TODO(김민경): add the calls this feature needs, e.g.
//   export const getReport = (meetingId: string) =>
//     api.audio<GapReport>(`/reports/${meetingId}`);

import type { Utterance } from "./types";

/** The transcript so far for a meeting being recorded.
 *
 * `Utterance[]`, not `TranscriptReady`. `TranscriptReady` is the payload A
 * publishes once, when the meeting is over, and a consumer only accepts it with
 * `privacy.original_audio_deleted` true — which a mid-recording snapshot cannot
 * honestly claim, because the recording still exists. `audio.md` says the live
 * channel goes straight to the screen and "never through a contract or an
 * event"; typing it as one was the contract leaking into the place that was
 * built to avoid it. `Utterance` is still a contract type, so no mirror is
 * hand-written here.
 */
export const getLiveTranscript = (meetingId: string) =>
  api.audio<Utterance[]>(`/meetings/${meetingId}/transcript`);
