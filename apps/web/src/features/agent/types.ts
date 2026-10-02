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
  /** A page in the app where the source can be read in full, when there is one. */
  href?: string | null;
  /** Approved, but the outcome was never recorded; a person must check it. */
  needs_check: boolean;
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
