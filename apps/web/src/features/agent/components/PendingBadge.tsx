"use client";

import { usePendingCount } from "../pendingCount";

/**
 * The count beside 승인 대기 in the sidebar: how many proposals of the team
 * being looked at wait for this person -- what 승인 대기 lists. Nothing is
 * drawn while none do. Other teams' proposals are a dot beside each team in
 * the team menu (`usePendingTeams`).
 *
 * It replaced a red dot on the assistant's launcher, which said that
 * something waited without saying what or where (the user, 2026-10-13). A
 * number on the entry that leads to the queue needs no explaining.
 */
export function PendingBadge({
  pathname,
  teamId,
}: {
  pathname: string;
  teamId: string | null;
}) {
  const count = usePendingCount(pathname, teamId);
  if (count === 0) return null;
  return (
    <span
      className="inline-flex items-center justify-center rounded-full bg-[var(--color-signal-critical)] text-[var(--color-accent-on-accent)]"
      style={{
        minWidth: 18,
        height: 18,
        padding: "0 5px",
        fontSize: 11,
        fontWeight: 600,
        lineHeight: 1,
      }}
    >
      <span aria-hidden>{count > 99 ? "99+" : count}</span>
      <span className="sr-only">, 승인할 제안 {count}건</span>
    </span>
  );
}
