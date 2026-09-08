/** Calls to /api/gap. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

export { api };

// TODO(박재경): add the calls this feature needs, e.g.
//   export const getReport = (meetingId: string) =>
//     api.gap<GapReport>(`/reports/${meetingId}`);
