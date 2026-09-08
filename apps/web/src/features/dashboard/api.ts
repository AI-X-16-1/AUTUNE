/** Calls to /api/intelligence. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

export { api };

// TODO(이승환): add the calls this feature needs, e.g.
//   export const getReport = (meetingId: string) =>
//     api.intelligence<GapReport>(`/reports/${meetingId}`);
