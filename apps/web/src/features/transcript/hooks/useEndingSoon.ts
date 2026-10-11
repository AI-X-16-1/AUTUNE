"use client";

import { useEffect, useState } from "react";

import { END_ALERT_LEAD_MS, plannedEndOf } from "../plannedEnd";

/**
 * Whether the meeting this tab is recording is within five minutes of the end
 * the tab was told (`plannedEnd`). False when it was told none.
 *
 * One timer to the moment, not a poll: the tab is open for the whole
 * recording, so nothing but the clock changes the answer. It stays true past
 * the planned end — a meeting that runs over is still "about to end" — until
 * the screen stops asking.
 *
 * `active` is the recording itself: nothing is timed before it starts or
 * after it stops.
 */
export function useEndingSoon(meetingId: string, active: boolean): boolean {
  const [soon, setSoon] = useState(false);

  useEffect(() => {
    if (!active) {
      setSoon(false);
      return;
    }
    const endsAt = plannedEndOf(meetingId);
    if (endsAt === null) return;
    const wait = new Date(endsAt).getTime() - END_ALERT_LEAD_MS - Date.now();
    if (wait <= 0) {
      setSoon(true);
      return;
    }
    const timer = window.setTimeout(() => setSoon(true), wait);
    return () => window.clearTimeout(timer);
  }, [meetingId, active]);

  return soon;
}
