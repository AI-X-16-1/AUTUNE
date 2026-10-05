import type { Route } from "next";

import type { PendingAction } from "./types";

/**
 * Where an approver reads the draft a report post would publish: E's
 * dashboard card opens one report on `#report-<meeting id>` (#642, #571).
 * Null for any other proposal, or one about no meeting.
 */
export function reportLink(item: PendingAction): Route | null {
  if (item.tool !== "intelligence.publish_meeting_report" || !item.meeting_id)
    return null;
  return `/dashboard#report-${encodeURIComponent(item.meeting_id)}` as Route;
}
