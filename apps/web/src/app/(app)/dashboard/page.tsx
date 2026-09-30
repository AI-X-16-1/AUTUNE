"use client";

import { Dashboard } from "@/features/dashboard";
import { TeamScope } from "@/features/transcript";

/**
 * S26, the team dashboard.
 *
 * Assembly only: the dashboard is module E's and takes a team id; `TeamScope`
 * is module A's and knows which teams this person belongs to. Replaces
 * `/dev-dashboard` as the way to reach the screen — that route asks for a team
 * id in the query string and 404s outside development.
 */
export default function DashboardPage() {
  return (
    <main style={{ padding: "var(--space-24) var(--space-page)" }}>
      <TeamScope>{(teamId) => <Dashboard teamId={teamId} />}</TeamScope>
    </main>
  );
}
