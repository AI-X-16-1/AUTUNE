"use client";

import { DecisionLineagePanel } from "@/features/context";
import { TeamScope } from "@/features/transcript";

/**
 * S22, decision lineage — every decision thread in the team, and how each one
 * changed.
 *
 * Assembly only: the panel is module D's and takes a team id; `TeamScope` is
 * module A's and knows which teams this person belongs to. This file hands one
 * to the other. A client component because the hand-off is a render function,
 * which cannot cross the server/client boundary.
 */
export default function DecisionsPage() {
  return (
    <main style={{ padding: "var(--space-24) var(--space-page)" }}>
      <TeamScope>{(teamId) => <DecisionLineagePanel teamId={teamId} />}</TeamScope>
    </main>
  );
}
