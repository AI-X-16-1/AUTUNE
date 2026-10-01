"use client";

import { useEffect, useState } from "react";

import { getMeeting } from "../api";

/**
 * A meeting's title, fetched once.
 *
 * `useMeeting` would poll for as long as the meeting is `recording`; a title
 * does not change while somebody records, so one read is enough. A failed
 * read returns `null` — a missing subtitle is not worth an error.
 */
export function useMeetingTitle(meetingId: string): string | null {
  const [title, setTitle] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    getMeeting(meetingId)
      .then((meeting) => {
        if (current) setTitle(meeting.title);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [meetingId]);
  return title;
}
