import { listActionItems, listJiraOpenIssues } from "./api";
import { rowTitle } from "./title";
import type { ActionStatus } from "./types";

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

/** A to-do somebody took and nobody finished: on the board's two middle columns. */
const UNFINISHED: ReadonlySet<ActionStatus> = new Set(["todo", "in_progress"]);

/**
 * What module B has for the agenda draft in the pre-meeting brief (#1147):
 * the to-dos of the earlier meeting that are not finished, in the order the
 * server lists them. Module D's brief panel names the shape; the route hands
 * this to it.
 *
 * **One meeting's** (the user, 2026-10-09: "지난 회의 한 건의 미완료 할 일"):
 * the one the brief already chose as the earlier meeting, asked by its id.
 * Not the team's whole backlog, and not `carried-over`, which answers for
 * every meeting held before one.
 *
 * Unfinished is 진행 전 and 진행 중. 확인 필요 is left out — nobody has said
 * yet that it is a to-do at all — and so is 완료. An item with no status is
 * 확인 필요 to the board (`columnOf`), and is left out the same.
 *
 * **A line is the item's top line as the board draws it, and nothing else**
 * (the user: "제목만"): no assignee and no due date. The draft is about what
 * to take up, not about who is late.
 */
export const EARLIER_ITEMS_AGENDA = {
  label: "지난 회의의 미완료 할 일",
  lines: async (earlierMeetingId: string) =>
    (await listActionItems({ meeting_id: earlierMeetingId }))
      .filter((item) => item.status !== undefined && UNFINISHED.has(item.status))
      .map((item) => ({ title: rowTitle(item.title, item.description).shown })),
};
