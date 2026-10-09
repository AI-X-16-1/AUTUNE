import { listDecisionThreads } from "./api";

/**
 * What module D has that an agenda can be drafted from (#1147): the team's
 * decisions, most recently changed first — the list S22 already shows
 * (`GET /api/context/decisions`). The new-meeting form is module A's and names
 * the shape; the route hands this to it.
 *
 * This is the whole of D's part in the form's draft: one read of a route D
 * already has, and nothing stored. It is not the pre-meeting brief — that
 * belongs to one meeting and is composed ten minutes before it starts, and the
 * form has no meeting yet.
 */
export const DECISIONS_AGENDA = {
  label: "최근 회의의 결정",
  lines: async (teamId: string) =>
    [...(await listDecisionThreads({ team_id: teamId }))]
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      .map((decision) => ({
        title: decision.topic_label,
        detail: decision.updated_at.slice(0, 10),
      })),
};
