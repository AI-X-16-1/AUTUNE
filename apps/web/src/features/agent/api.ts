/**
 * The agent layer's approval queue and its approvers. This feature calls
 * `/api/agent` only.
 */
import { api } from "@/shared/api/client";

import type {
  Approvers,
  ApproverScope,
  PendingAction,
  RejectReason,
} from "./types";

export const listPending = () => api.agent<PendingAction[]>("/pending");

export const approvePending = (id: string) =>
  api.agent<PendingAction>(`/pending/${encodeURIComponent(id)}/approve`, {
    method: "POST",
  });

export const rejectPending = (id: string, reason: RejectReason) =>
  api.agent<PendingAction>(`/pending/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });

export const listApprovers = (teamId: string) =>
  api.agent<Approvers>(`/approvers?team_id=${encodeURIComponent(teamId)}`);

export const setApproverScopes = (
  teamId: string,
  userId: string,
  scopes: ApproverScope[],
) =>
  api.agent<Approvers["members"][number]>(
    `/approvers/${encodeURIComponent(userId)}?team_id=${encodeURIComponent(teamId)}`,
    { method: "PUT", body: JSON.stringify({ scopes }) },
  );
