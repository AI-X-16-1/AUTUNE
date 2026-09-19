/** Calls to /api/extraction. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

import type { ActionItemDetail, ActionItemRead, ActionStatus, ExtractionResult } from "./types";

export { api };

/** Everything this meeting produced: classifications, items, decisions. */
export const getResults = (meetingId: string) =>
  api.extraction<ExtractionResult>(`/results/${meetingId}`);

/**
 * Every field optional; the server ANDs the ones given. `due_before` is strict —
 * today's date asks for what is overdue, and an undated item never matches it.
 */
export interface ActionItemFilter {
  meeting_id?: string;
  assignee_id?: string;
  status?: ActionStatus;
  due_before?: string;
}

export const listActionItems = (filter: ActionItemFilter = {}) => {
  const query = new URLSearchParams(
    Object.entries(filter).filter(([, value]) => value !== undefined) as [string, string][],
  ).toString();
  return api.extraction<ActionItemRead[]>(`/action-items${query ? `?${query}` : ""}`);
};

/**
 * One item with the words of the utterances it came from, for the drawer.
 *
 * The only call in this feature that brings utterances back verbatim, and it
 * asks for one item's at a time — the list carries their ids only. Call it when
 * a quotation is about to be shown, not to prefetch a board.
 */
export const getActionItem = (id: string) =>
  api.extraction<ActionItemDetail>(`/action-items/${encodeURIComponent(id)}`);

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
  api.extraction<ActionItemRead>("/action-items", {
    method: "POST",
    body: JSON.stringify(draft),
  });

export const updateActionItem = (id: string, changes: Partial<ActionItemDraft & { status: ActionStatus }>) =>
  api.extraction<ActionItemRead>(`/action-items/${id}`, {
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
/**
 * Delete one item. The server answers 204 with no body.
 *
 * `request` parses every 2xx body as JSON, so an empty 204 throws a
 * `SyntaxError` after the row is already gone — the board then kept the card
 * and, once the drawer reported failures, said the delete had failed. An error
 * status still arrives as `ApiError`; only the parse of an empty success is
 * dropped here. The shared client is the better place for this (all five own
 * it); until then it stays in this feature.
 */
export const deleteActionItem = async (id: string): Promise<void> => {
  try {
    await api.extraction<void>(`/action-items/${id}`, { method: "DELETE" });
  } catch (cause) {
    if (cause instanceof SyntaxError) return;
    throw cause;
  }
};

/** Re-push this meeting's items to Notion and Jira. */
export const syncResults = (meetingId: string) =>
  api.extraction<void>(`/results/${meetingId}/sync`, { method: "POST" });
