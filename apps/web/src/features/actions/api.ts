/** Calls to /api/extraction. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

import type { ActionItem, ActionStatus, ExtractionResult } from "./types";

export { api };

/** Everything this meeting produced: classifications, items, decisions. */
export const getResults = (meetingId: string) =>
  api.extraction<ExtractionResult>(`/results/${meetingId}`);

export interface ActionItemFilter {
  assignee_id?: string;
  status?: ActionStatus;
  due_before?: string;
}

export const listActionItems = (filter: ActionItemFilter = {}) => {
  const query = new URLSearchParams(
    Object.entries(filter).filter(([, value]) => value !== undefined) as [string, string][],
  ).toString();
  return api.extraction<ActionItem[]>(`/action-items${query ? `?${query}` : ""}`);
};

export interface ActionItemDraft {
  meeting_id: string;
  description: string;
  assignee_id?: string | null;
  assignee_label?: string | null;
  due_date?: string | null;
  source_utterance_ids?: string[];
}

/**
 * Add an item the model missed.
 *
 * ADR 0006 ranks recall above precision because a wrong item costs a click and
 * a missing one costs re-reading the meeting — but recall alone does not close
 * that gap, since the user still has to notice the absence. This is the path
 * that lets them fix it, and without it the decision does not hold.
 */
export const createActionItem = (draft: ActionItemDraft) =>
  api.extraction<ActionItem>("/action-items", {
    method: "POST",
    body: JSON.stringify(draft),
  });

export const updateActionItem = (id: string, changes: Partial<ActionItemDraft & { status: ActionStatus }>) =>
  api.extraction<ActionItem>(`/action-items/${id}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });

/**
 * Remove an item the model got wrong. **The row is gone, not flagged.**
 *
 * `docs/architecture/privacy.md` allows no soft deletes and no tombstones
 * holding content. Nothing in this feature may offer an undo that restores the
 * text, or a trash view, or a "deleted" filter — there is nothing left to show.
 * The server records that a deletion happened, which is all the edit-cost metric
 * asks for.
 */
export const deleteActionItem = (id: string) =>
  api.extraction<void>(`/action-items/${id}`, { method: "DELETE" });

/** Re-push this meeting's items to Notion and Jira. */
export const syncResults = (meetingId: string) =>
  api.extraction<void>(`/results/${meetingId}/sync`, { method: "POST" });
