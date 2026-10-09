import { getReport, listTeamGaps } from "./api";

/**
 * What module C has that an agenda can be drafted from (#1147): the team's
 * open HIGH gaps across its meetings (`GET /api/gap/gaps`, #550), each with
 * the meeting it came from. The new-meeting form is module A's and names the
 * shape; the route hands this to it.
 *
 * Named for what the list is. It is **not** the gaps somebody sent on to the
 * next meeting ("다음 회의로 넘기기", #824): no route returns those marks. As
 * on the team list, a line is a gap and never a person.
 */
export const OPEN_GAPS_AGENDA = {
  label: "이전 회의의 미해결 갭",
  lines: async (teamId: string) =>
    (await listTeamGaps(teamId, ["high"])).map((gap) => ({
      title: gap.title,
      detail: `${gap.meeting_title} · ${gap.meeting_date.slice(0, 10)}`,
    })),
};

/**
 * What module C has for the agenda draft in the pre-meeting brief (#1147):
 * the gaps the earlier meeting's report still carries, in the report's order.
 * Module D's brief panel names the shape; the route hands this to it.
 *
 * A dismissed gap is not in a report, so what is listed is what nobody closed.
 * Every severity, as C's own `open_gaps` tool reads a meeting for the
 * Briefing subagent — the panel lists five and counts the rest.
 */
export const EARLIER_GAPS_AGENDA = {
  label: "지난 회의의 미해결 갭",
  lines: async (earlierMeetingId: string) =>
    ((await getReport(earlierMeetingId)).gaps ?? []).map((gap) => ({ title: gap.title })),
};
