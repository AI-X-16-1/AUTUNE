/** Calls to /api/extraction. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

export { api };

// TODO(강민구): add the calls this feature needs, e.g.
//   export const getReport = (meetingId: string) =>
//     api.extraction<GapReport>(`/reports/${meetingId}`);
