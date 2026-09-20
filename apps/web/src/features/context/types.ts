/**
 * Types for this feature.
 *
 * Anything that crosses the API boundary comes from @autune/contracts, which is
 * generated from the Pydantic models. Never hand-write a mirror of a contract.
 */
export type { ChangeType, NliLabel } from "@autune/contracts";

import type { ChangeType, NliLabel } from "@autune/contracts";

/** A link's lifecycle — `asserted`/`confirmed` are shown settled; `rejected` never comes back. */
export type TopicLinkStatus = "asserted" | "confirmed" | "pending" | "rejected";

/**
 * One topic link as `/api/context` returns it — `TopicLinkRead` in
 * `modules/context/src/autune_context/schemas.py`.
 *
 * **Not a contract, so it is not generated.** `TopicLink` in `@autune/contracts`
 * is D's pub/sub payload to E; this is D's own read-API response, which nobody
 * else parses. `linked_meeting_id`/`linked_meeting_date` are nullable here even
 * though the contract's are not — the retention sweep can delete the linked
 * meeting out from under a link row that survives it (see the schema's own
 * docstring).
 */
export interface TopicLinkRead {
  id: number;
  meeting_id: string;
  topic_label: string;
  linked_meeting_id: string | null;
  linked_meeting_date: string | null;
  similarity: number;
  rerank_score: number;
  confidence: number;
  status: TopicLinkStatus;
}

/** A meeting's topic links, split the way S22/S15 render them (`GET /links/{meeting_id}`). */
export interface TopicLinksRead {
  asserted: TopicLinkRead[];
  pending: TopicLinkRead[];
}

/**
 * One version of a decision, as seen in one meeting — `DecisionVersionRead`.
 *
 * `previous_statement`/`previous_meeting_id` come back `null` once the
 * predecessor they quote has left the retention window, even though this
 * version itself is still visible — never treat a `null` pair as "this was the
 * original version" without checking whether it is also the first in the
 * `versions` array.
 *
 * Has no `key_stakeholders_absent` — the backend withholds it here until
 * route auth exists (#156, see #188). It still reaches the absent person
 * directly, by Slack DM.
 */
export interface DecisionVersionRead {
  id: number;
  meeting_id: string;
  current_statement: string;
  previous_statement: string | null;
  previous_meeting_id: string | null;
  change_type: ChangeType;
  nli_label: NliLabel | null;
  confidence: number;
  created_at: string;
}

/** A thread's full timeline, oldest version first (`GET /decisions/{thread_id}`). */
export interface DecisionLineageRead {
  thread_id: string;
  topic_label: string;
  versions: DecisionVersionRead[];
}

/** One thread's current head, for the team-wide list (`GET /decisions`). */
export interface DecisionSummaryRead {
  thread_id: string;
  topic_label: string;
  meeting_id: string;
  change_type: ChangeType;
  confidence: number;
  updated_at: string;
}
