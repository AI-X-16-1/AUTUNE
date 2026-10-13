import {
  listActionItems,
  listJiraOpenIssues,
  listSinceLastMeeting,
  listSinceMeeting,
  type SinceLastMeeting,
} from "./api";
import { SINCE_PARTS } from "./sinceParts";
import { rowTitle } from "./title";
import type { ActionItemRead, ActionStatus } from "./types";

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

const unfinished = (item: ActionItemRead) =>
  item.status !== undefined && UNFINISHED.has(item.status);

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
      .filter(unfinished)
      .map((item) => ({ title: rowTitle(item.title, item.description).shown })),
};

/**
 * The parts of "지난 회의 이후" an agenda draft takes (module D's owner on
 * #1270): 끝내지 않고 닫힌 일 and 늦은 일, in the board's order. The draft is
 * headed "이번 회의에서 다룰 문제", so it takes the two a team has to talk
 * about; 끝난 일, 시작한 일 and 새로 생긴 일 stay on the board's section.
 */
const DRAFT_PARTS = SINCE_PARTS.filter((part) => part.key === "closed" || part.key === "late");

/**
 * One answer as the draft's lines: part after part, an item's top line with
 * the name of the part it is in -- no assignee, no due date, and nothing
 * counted, as the other sources here.
 *
 * **An item is listed once** (#1270). An unfinished to-do of `countedFrom`
 * is already in the draft under `EARLIER_ITEMS_AGENDA`, so it is left out
 * here, by its id. An id the item list does not hold -- deleted between the
 * two reads -- is left out too.
 */
function draftLines(
  answer: SinceLastMeeting,
  items: readonly ActionItemRead[],
  countedFrom: string | null,
) {
  const byId = new Map(items.map((item) => [item.id, item]));
  const alreadyListed = new Set(
    items
      .filter((item) => item.meeting_id === countedFrom && unfinished(item))
      .map((item) => item.id),
  );
  return DRAFT_PARTS.flatMap((part) =>
    answer[part.key].flatMap((id) => {
      const item = byId.get(id);
      return item === undefined || alreadyListed.has(id)
        ? []
        : [{ title: rowTitle(item.title, item.description).shown, detail: part.label }];
    }),
  );
}

/** One name for the form's row and the brief's draft: the row names what the draft shows. */
const SINCE_LABEL = "지난 회의 이후 늦거나 닫힌 할 일";

/**
 * What the new-meeting form's agenda row reads to say whether a draft will
 * have this source in it (#1270): the team's to-dos that are late, or were
 * closed without being finished since its last analysed meeting. The form is
 * module A's and names the shape; the route hands this to it.
 *
 * Read by the server, now, from Autune's own rows, and stored nowhere. The
 * meeting counted from is the one this module picks for the team; the brief
 * counts from the one module D picks (`SINCE_EARLIER_MEETING_AGENDA`), which
 * need not be the same one: the row says a draft can have this in it, and the
 * brief decides what it has.
 *
 * A team with no analysed meeting has no lines: its answer's parts are empty.
 */
export const SINCE_LAST_MEETING_AGENDA = {
  label: SINCE_LABEL,
  lines: async (teamId: string) => {
    const [answers, items] = await Promise.all([listSinceLastMeeting(teamId), listActionItems()]);
    return answers
      .filter((answer) => answer.team_id === teamId)
      .flatMap((answer) => draftLines(answer, items, answer.meeting_id));
  },
};

/**
 * The same two parts for the agenda draft in the pre-meeting brief (#1270),
 * **counted from the earlier meeting the brief already chose** and asked by
 * its id: the draft does not pick a second "previous meeting" of its own.
 * Module D's brief panel names the shape; the route hands this to it.
 *
 * 늦은 일 asks only for today, so it is the team's late to-dos whichever
 * meeting is counted from; 끝내지 않고 닫힌 일 is what was closed since that
 * meeting. A meeting that cannot be counted from has no lines.
 */
export const SINCE_EARLIER_MEETING_AGENDA = {
  label: SINCE_LABEL,
  lines: async (earlierMeetingId: string) => {
    const [answers, items] = await Promise.all([
      listSinceMeeting(earlierMeetingId),
      listActionItems(),
    ]);
    return answers.flatMap((answer) => draftLines(answer, items, earlierMeetingId));
  },
};
