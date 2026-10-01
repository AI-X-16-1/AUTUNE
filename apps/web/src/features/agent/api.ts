/**
 * The agent layer's approval queue. This feature calls `/api/agent` only.
 */
import { api } from "@/shared/api/client";

import type { PendingAction, RejectReason } from "./types";

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
