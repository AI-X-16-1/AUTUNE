/**
 * When a live meeting is planned to end — kept in this browser tab, and
 * nowhere else.
 *
 * S06 draws an optional end time and a row that uses it ("종료 5분 전 …
 * 알림"). `Meeting` has no planned end and the create payload has no field
 * for one, so the smallest place that makes the row true is the tab that is
 * about to record: the new-meeting screen writes it as it opens S13, and S13
 * reads it. `sessionStorage` rather than component state because the two are
 * different routes, and so that reloading S13 does not lose it.
 *
 * What that costs, and the screen says so: the time is known only to this
 * tab. A meeting saved for later and recorded from another tab or device has
 * none, and nothing on the server can act on it. If the planned end moves to
 * the server (#1147, A2), this file is the one reader and writer to change.
 *
 * Nothing here is about a person: a meeting id and an instant.
 */

const KEY = "autune.plannedEnd.";

/** Five minutes: S14's "종료 5분 전". */
export const END_ALERT_LEAD_MS = 5 * 60_000;

function store(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    // Storage can be refused outright (a private window, a blocked site).
    return null;
  }
}

export function rememberPlannedEnd(meetingId: string, endsAt: string): void {
  try {
    store()?.setItem(KEY + meetingId, endsAt);
  } catch {
    // A full or refused store: the meeting records without the alert.
  }
}

/** The planned end as an ISO instant, or null when this tab was told none. */
export function plannedEndOf(meetingId: string): string | null {
  try {
    const kept = store()?.getItem(KEY + meetingId) ?? null;
    return kept !== null && !Number.isNaN(new Date(kept).getTime()) ? kept : null;
  } catch {
    return null;
  }
}

export function forgetPlannedEnd(meetingId: string): void {
  try {
    store()?.removeItem(KEY + meetingId);
  } catch {
    // Nothing to forget in a store that cannot be read.
  }
}

/**
 * A clock time typed on the new-meeting screen ("15:30"), as the next instant
 * the clock reads it: later today, or tomorrow when that time has already
 * passed today — a meeting opened at 23:50 to end at 00:30. Null for anything
 * that is not a time.
 */
export function nextInstantAt(time: string, now: Date = new Date()): string | null {
  const match = /^(\d{2}):(\d{2})$/.exec(time);
  if (!match) return null;
  const [hours, minutes] = [Number(match[1]), Number(match[2])];
  if (hours > 23 || minutes > 59) return null;
  const at = new Date(now);
  at.setHours(hours, minutes, 0, 0);
  if (at.getTime() <= now.getTime()) at.setDate(at.getDate() + 1);
  return at.toISOString();
}
