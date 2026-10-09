import { listJiraOpenIssues } from "./api";

/**
 * What module B has that an agenda can be drafted from (#1147): the team's
 * open Jira issues. The new-meeting form is module A's and names the shape
 * (`label`, `lines`); the route hands this to it.
 *
 * Read from Jira by the server now, in Jira's order, and stored nowhere. The
 * key and the status are the line under the summary; **the assignee is left
 * out** — a draft agenda is about what to take up, not about who.
 *
 * The team's 자료 are not a source yet: #1147 left the material source after
 * 10-12 (B2), with #817's ingest.
 */
export const JIRA_AGENDA = {
  label: "열린 Jira 이슈",
  lines: async (teamId: string) =>
    (await listJiraOpenIssues())
      .filter((project) => project.team_id === teamId)
      .flatMap((project) => project.issues)
      .map((issue) => ({
        title: issue.summary,
        detail: [issue.key, issue.status].filter(Boolean).join(" · "),
      })),
};
