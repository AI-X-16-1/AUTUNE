import { useEffect, useState } from "react";

import { listPending } from "./api";

/**
 * How many proposals wait for the signed-in person to decide.
 *
 * Shown in two places: beside 승인 대기 in the sidebar, and as the first line
 * of the assistant when it opens. Both read the same queue (`GET
 * /api/agent/pending`), and only rows still `pending` count: an approval
 * interrupted mid-run (`needs_check`) is never re-run, so it is nothing to
 * approve (#759 review).
 *
 * Read again on every page, and whenever a proposal is queued or decided on
 * this page (`announcePendingChanged`): deciding one on 승인 대기 or on a chat
 * card does not change the path, and the count beside the sidebar entry would
 * otherwise stay where it was. A queue that cannot be read counts as nothing
 * waiting -- a number shown on a failed read would be a guess.
 */
const CHANGED = "autune:pending-changed";

export function announcePendingChanged(): void {
  window.dispatchEvent(new Event(CHANGED));
}

export function usePendingCount(pathname: string, refresh?: unknown): number {
  const [count, setCount] = useState(0);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const heard = () => setTick((t) => t + 1);
    window.addEventListener(CHANGED, heard);
    return () => window.removeEventListener(CHANGED, heard);
  }, []);

  useEffect(() => {
    let current = true;
    listPending()
      .then((rows) => {
        if (current)
          setCount(rows.filter((r) => r.status === "pending").length);
      })
      .catch(() => {
        if (current) setCount(0);
      });
    return () => {
      current = false;
    };
  }, [pathname, refresh, tick]);

  return count;
}
