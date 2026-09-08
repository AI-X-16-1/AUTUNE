/** Calls to /api/context. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

export { api };

// TODO(문민재): add the calls this feature needs, e.g.
//   export const getReport = (meetingId: string) =>
//     api.context<GapReport>(`/reports/${meetingId}`);
