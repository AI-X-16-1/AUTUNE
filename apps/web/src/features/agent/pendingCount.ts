import { useEffect, useMemo, useSyncExternalStore } from "react";

import { listPending } from "./api";

/**
 * How many proposals wait for the signed-in person to decide, per team.
 *
 * Shown in three places: beside 승인 대기 in the sidebar and as the open
 * assistant's first line, both for the team being looked at, and as a dot
 * beside every team in the sidebar's team menu that has any (the user,
 * 2026-10-13). 승인 대기 lists one team at a time, so a team not being looked
 * at needs a sign of its own, or what waits there is missed.
 *
 * **One read for all of them.** Each place used to read the queue itself, so
 * one page asked the same question two or three times (#1309 review). The
 * answer is kept here, once per page, and every place reads it from here. A
 * read already under way is joined rather than repeated.
 *
 * Read again on every page and whenever a proposal is queued or decided on
 * this page (`announcePendingChanged`): deciding one on 승인 대기 or on a chat
 * card does not change the path, and the counts would otherwise stay where
 * they were. Only rows still `pending` count: an approval interrupted mid-run
 * (`needs_check`) is never re-run, so it is nothing to approve (#759 review).
 * A queue that cannot be read counts as nothing waiting -- a number shown on a
 * failed read would be a guess.
 */
type Counts = ReadonlyMap<string, number>;

const NONE: Counts = new Map();
let counts: Counts = NONE;
let reading = false;
let again = false;
const listeners = new Set<() => void>();

function publish(next: Counts): void {
  counts = next;
  listeners.forEach((listener) => listener());
}

/**
 * Read the queue. A read under way is joined; after a change (`changed`) one
 * more read follows it, since what it is reading may predate the change.
 */
function refresh(changed: boolean): void {
  if (reading) {
    if (changed) again = true;
    return;
  }
  reading = true;
  listPending()
    .then((rows) => {
      const next = new Map<string, number>();
      for (const row of rows)
        if (row.status === "pending")
          next.set(row.team_id, (next.get(row.team_id) ?? 0) + 1);
      publish(next);
    })
    .catch(() => publish(NONE))
    .finally(() => {
      reading = false;
      if (again) {
        again = false;
        refresh(false);
      }
    });
}

const CHANGED = "autune:pending-changed";

export function announcePendingChanged(): void {
  window.dispatchEvent(new Event(CHANGED));
}

const onChanged = () => refresh(true);

function subscribe(listener: () => void): () => void {
  // One window listener however many places show a count.
  if (listeners.size === 0) window.addEventListener(CHANGED, onChanged);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) window.removeEventListener(CHANGED, onChanged);
  };
}

/**
 * Every team's count, read again when `pathname` or `refreshOn` changes or a
 * change is announced. Teams with nothing waiting are absent.
 */
export function usePendingCounts(pathname: string, refreshOn?: unknown): Counts {
  useEffect(() => {
    refresh(false);
  }, [pathname, refreshOn]);
  return useSyncExternalStore(
    subscribe,
    () => counts,
    () => NONE,
  );
}

/** One team's count; 0 while no team is known. */
export function usePendingCount(
  pathname: string,
  teamId: string | null,
  refreshOn?: unknown,
): number {
  const all = usePendingCounts(pathname, refreshOn);
  return teamId === null ? 0 : (all.get(teamId) ?? 0);
}

/** The teams with anything waiting, for the sidebar's team menu. */
export function usePendingTeams(pathname: string): ReadonlySet<string> {
  const all = usePendingCounts(pathname);
  return useMemo(() => new Set(all.keys()), [all]);
}

/** Tests only: forget what was read, so one test's counts do not reach the next. */
export function resetPendingCounts(): void {
  counts = NONE;
  reading = false;
  again = false;
}
