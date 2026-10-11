"use client";

import { useEffect, useState } from "react";

import { getMeeting } from "../api";

/**
 * The meeting's own team, read once and only when somebody needs it.
 *
 * The live screen does not otherwise need a team (see `LiveMeetingScreen`);
 * the end-of-meeting slot does, because what the page puts there is another
 * module's and is asked by team. `wanted` keeps a meeting with no alert from
 * making the request at all. A failed read is `null`: the slot stays empty.
 */
export function useMeetingTeam(meetingId: string, wanted: boolean): string | null {
  const [teamId, setTeamId] = useState<string | null>(null);
  useEffect(() => {
    if (!wanted) return;
    let current = true;
    getMeeting(meetingId)
      .then((meeting) => {
        if (current) setTeamId(meeting.team_id);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [meetingId, wanted]);
  return teamId;
}
