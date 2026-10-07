/** One proposal waiting for a decision (`GET /api/agent/pending`). */
export type PendingAction = {
  id: string;
  team_id: string;
  meeting_id: string | null;
  subagent: string;
  kind: string;
  tool: string;
  status: "pending" | "approved" | "rejected" | "superseded" | "failed";
  reject_reason: string | null;
  result_ok: boolean | null;
  created_at: string;
  decided_at: string | null;
  title: string;
  body: string;
  /** Approved, but the outcome was never recorded; a person must check it. */
  needs_check: boolean;
  /** The card's meeting, read when the list is read (#854). */
  meeting_title?: string | null;
  meeting_started_at?: string | null;
};

export type RejectReason =
  "wrong_evidence" | "not_now" | "handled_elsewhere" | "other";

/** A scope an approver decides for; `any` also lets them change the approvers. */
export type ApproverScope =
  "any" | "report" | "research" | "followup" | "workload";

/** A team's members and who decides what (`GET /api/agent/approvers`). */
export type Approvers = {
  /** Nobody is an approver yet, or the caller holds `any`. */
  can_manage: boolean;
  scopes: ApproverScope[];
  members: { user_id: string; name: string; scopes: ApproverScope[] }[];
};

/** One ranked item an answer rests on (`Finding` in `autune_agent.results`). */
export type ChatFinding = {
  title: string;
  body: string;
  score: number;
  /** Extra keys a module adds; `meeting_id` makes the row a link. */
  id?: string;
  meeting_id?: string;
};

/** One chat turn's reply (`POST /api/agent/chat`). */
export type ChatReply = {
  run_id: string;
  outcome: "answered" | "unrouted" | string;
  route: string | null;
  answer: string;
  items: ChatFinding[];
  /** Actions the subagent proposed, at any level. */
  proposed: number;
  /** L1 actions that ran and worked. */
  executed: number;
  /** Proposals this run left waiting for an approver, counted on the server. */
  queued: number;
  /** L2 proposals this run queued that the caller may decide (plan mode's rule). */
  pending: PendingAction[];
  /** L1 actions that did not go through, each with why (#862). */
  unfinished?: ChatUnfinished[];
};

/** An L1 action that did not go through: its title and the reason in words. */
export type ChatUnfinished = {
  title: string;
  reason: string;
};
