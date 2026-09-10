/** Calls to /api/audio. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

export { api };

// TODO(김민경): add the calls this feature needs, e.g.
//   export const getReport = (meetingId: string) =>
//     api.audio<GapReport>(`/reports/${meetingId}`);

import type { TranscriptReady } from "./types";

/** The transcript so far for a meeting being recorded. */
export const getLiveTranscript = (meetingId: string) =>
  api.audio<TranscriptReady>(`/meetings/${meetingId}/transcript`);
