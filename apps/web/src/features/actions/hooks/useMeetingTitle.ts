"use client";

import { useEffect, useState } from "react";

import { getSummary } from "../api";

/**
 * The meeting's title, for the heading of a screen that is about one meeting.
 *
 * Read from the meeting's summary (module B's own route, the one the minutes
 * page uses), which carries the title already. ``null`` while it is being
 * read, when the read fails, and for a blank title: the caller draws nothing
 * then. It never falls back to the meeting's id -- an internal id is not a
 * name a person can use, and a line that is sometimes an id is worse than a
 * line that is sometimes missing.
 */
export function useMeetingTitle(meetingId: string): string | null {
  const [read, setRead] = useState<{ id: string; title: string | null } | null>(null);
  useEffect(() => {
    let alive = true;
    getSummary(meetingId)
      .then((answer) => {
        if (alive) setRead({ id: meetingId, title: answer.meeting_title?.trim() || null });
      })
      .catch(() => {
        if (alive) setRead({ id: meetingId, title: null });
      });
    return () => {
      alive = false;
    };
  }, [meetingId]);
  // A title read for another meeting is not this one's.
  return read !== null && read.id === meetingId ? read.title : null;
}
