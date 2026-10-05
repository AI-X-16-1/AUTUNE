"use client";

import { TeamGapList } from "@/features/gap";
import { TeamScope } from "@/features/transcript";

/**
 * The sidebar's "갭 리포트" — every open gap across the team's meetings (#550).
 *
 * Assembly only: the list is module C's and takes a team id; `TeamScope` is
 * module A's and knows which teams this person belongs to. The same hand-off as
 * `/decisions`, and a client component for the same reason.
 */
export default function GapsPage() {
  return (
    <main style={{ padding: "var(--space-24) var(--space-page)" }}>
      <TeamScope>{(teamId) => <TeamGapList teamId={teamId} />}</TeamScope>
    </main>
  );
}
