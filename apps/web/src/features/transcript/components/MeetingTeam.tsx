"use client";

import { useEffect } from "react";

import { getMeeting } from "../api";
import { rememberedTeam, rememberTeam } from "../selectedTeam";

/**
 * Opening a meeting makes its team the one being looked at (the user,
 * 2026-10-13).
 *
 * A meeting can be opened from anywhere -- 승인 대기, the assistant, a DM's
 * link -- and the sidebar kept marking the team chosen before, so a second
 * team's meeting sat under the first team's name, and every team-level screen
 * opened next showed the first team. The meeting's own team is now the
 * choice, through `rememberTeam` like any other, so the sidebar's menu and
 * every screen that follows the choice agree with the meeting on screen.
 *
 * Draws nothing. A meeting that cannot be read leaves the choice alone: the
 * screen says why it cannot show the meeting, and guessing a team would be
 * worse.
 */
export function MeetingTeam({ meetingId }: { meetingId: string }) {
  useEffect(() => {
    let current = true;
    getMeeting(meetingId)
      .then((meeting) => {
        if (current && meeting.team_id !== rememberedTeam()) rememberTeam(meeting.team_id);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [meetingId]);
  return null;
}
