import type { ActionItemRead } from "./types";

/** Meetings an open item may be carried through before it reads as stuck —
 * the server's `STALE_AFTER` (the user, 2026-10-04). */
export const STALE_AFTER = 3;

/** "N회 넘어감" for an open item carried through `STALE_AFTER` or more
 * meetings, or null. */
export function staleLabel(
  item: Pick<ActionItemRead, "status" | "carried_meetings">,
): string | null {
  const open = item.status === "todo" || item.status === "in_progress";
  const carried = item.carried_meetings ?? 0;
  return open && carried >= STALE_AFTER ? `${carried}회 넘어감` : null;
}
