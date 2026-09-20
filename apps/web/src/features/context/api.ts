/** Calls to /api/context. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

import type {
  DecisionLineageRead,
  DecisionSummaryRead,
  TopicLinksRead,
  TopicLinkRead,
} from "./types";

export { api };

/** This meeting's topic links, asserted (incl. user-confirmed) and pending (S15/S22). */
export const getLinks = (meetingId: string) =>
  api.context<TopicLinksRead>(`/links/${encodeURIComponent(meetingId)}`);

/** A user confirms or rejects a `pending` link. Only a `pending` link accepts this. */
export const confirmLink = (linkId: number, status: "confirmed" | "rejected") =>
  api.context<TopicLinkRead>(`/links/${linkId}/confirm`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });

/** A decision thread's full lineage timeline, oldest version first (S22). */
export const getDecisionThread = (threadId: string) =>
  api.context<DecisionLineageRead>(`/decisions/${encodeURIComponent(threadId)}`);

/** Every field optional except `team_id`; both are ANDed when given. */
export interface DecisionThreadFilter {
  team_id: string;
  topic?: string;
  change_type?: string;
}

/** A team's decision threads by their current head, for the S22 topic list. */
export const listDecisionThreads = (filter: DecisionThreadFilter) => {
  const { team_id, ...rest } = filter;
  const query = new URLSearchParams({
    team_id,
    ...Object.fromEntries(Object.entries(rest).filter(([, value]) => value !== undefined)),
  }).toString();
  return api.context<DecisionSummaryRead[]>(`/decisions?${query}`);
};
