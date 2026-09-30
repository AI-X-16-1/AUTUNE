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
};

export type RejectReason =
  "wrong_evidence" | "not_now" | "handled_elsewhere" | "other";
