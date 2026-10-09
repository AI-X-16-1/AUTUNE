"use client";

import type { ReactNode } from "react";

import { EndAlertBand } from "@/features/gap";
import { LiveMeetingScreen } from "@/features/transcript";

/**
 * The live route's assembly, as a client component.
 *
 * Assembly only. The live screen is module A's and knows when a recording is
 * five minutes from its planned end and which team the meeting belongs to; the
 * band it shows then is module C's and is asked by team (#1147). The two
 * features do not import each other, so the route joins them — and the join is
 * a function of the team id, which a server component cannot hand to a client
 * one. Hence this file beside `page.tsx`, which stays a server component and
 * passes the notice through.
 */
export function LiveMeeting({ meetingId, notice }: { meetingId: string; notice?: ReactNode }) {
  return (
    <LiveMeetingScreen
      meetingId={meetingId}
      notice={notice}
      endingSoon={(teamId) => <EndAlertBand teamId={teamId} exceptMeetingId={meetingId} />}
    />
  );
}
