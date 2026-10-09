import { listTeamGaps } from "./api";
import type { TeamGap } from "./types";

/**
 * What the end-of-meeting band reads and what it calls it — one seam, because
 * the two are one claim.
 *
 * Today: every open HIGH gap of the team (`GET /api/gap/gaps`, #550), named
 * for what that list is — gaps earlier meetings left open. It is **not** the
 * gaps somebody sent on to the next meeting ("다음 회의로 넘기기", #824): those
 * marks are read only by the agent's `carried_gaps` tool and no route returns
 * them, so the sentence must not say "넘어온".
 *
 * If a carried-gaps route exists (#1147, A4 가), `list` and `sentence` change
 * here together — to that route and "지난 회의에서 넘어온 사항 n건" — and
 * nothing else in the band moves.
 */
export const END_ALERT = {
  list: (teamId: string): Promise<TeamGap[]> => listTeamGaps(teamId, ["high"]),
  sentence: (count: number): string => `이전 회의의 미해결 갭 ${count}건`,
  none: "이전 회의의 미해결 갭이 없습니다",
} as const;

/** How many gaps the opened band lists; the count in its sentence is of all. */
export const END_ALERT_SHOWN = 5;
