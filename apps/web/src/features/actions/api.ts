/** Calls to /api/extraction. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

import type {
  ActionItemDetail,
  DecisionDetail,
  ActionItemRead,
  ActionStatus,
  CarriedOver,
  DecisionStatus,
  ExtractionResult,
  MeetingReview,
  MeetingSummary,
  ReviewDecision,
} from "./types";

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

/**
 * One decision with the words of the utterances it was settled in. As with
 * `getActionItem`, the list never carries them — ask for a row's when it is on
 * screen.
 */
export const getDecision = (id: string) =>
  api.extraction<DecisionDetail>(`/decisions/${encodeURIComponent(id)}`);

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
 *
 * The server answers 204 with no body, and `request` parses every 2xx body as
 * JSON, so an empty 204 threw a `SyntaxError` after the row was already gone —
 * the board kept the card, and once the drawer reported failures it said the
 * delete had failed. An error status still arrives as `ApiError`; only the parse
 * of an empty success is dropped (`withoutBody`). The shared client is the
 * better place for this (all five own it); until then it stays in this feature.
 */
export const deleteActionItem = (id: string) =>
  withoutBody(`/action-items/${encodeURIComponent(id)}`);

/** A `DELETE` answered 204: an empty success is not a parse failure. */
async function withoutBody(path: string): Promise<void> {
  try {
    await api.extraction<void>(path, { method: "DELETE" });
  } catch (cause) {
    if (cause instanceof SyntaxError) return;
    throw cause;
  }
}

/**
 * What the team's earlier meetings left open, for the popup a review opens with
 * (WBS 4.8). Counts cover everything; `items` is the most urgent ten.
 */
export const getCarriedOver = (meetingId: string) =>
  api.extraction<CarriedOver>(`/carried-over/${encodeURIComponent(meetingId)}`);

/** S15's 요약 tab (#421): the meeting's decisions, items, counts and memo. */
export const getSummary = (meetingId: string) =>
  api.extraction<MeetingSummary>(`/summary/${encodeURIComponent(meetingId)}`);

/** Replace the team's memo; a blank one removes it. Answers with the summary. */
export const putSummaryNote = (meetingId: string, body: string) =>
  api.extraction<MeetingSummary>(`/summary/${encodeURIComponent(meetingId)}/note`, {
    method: "PUT",
    body: JSON.stringify({ body }),
  });

/** Everything in one meeting that needs a person before it goes anywhere (#246). */
export const getReview = (meetingId: string) =>
  api.extraction<MeetingReview>(`/reviews/${encodeURIComponent(meetingId)}`);

/**
 * Confirm, reject or reword one decision. `pending` undoes a mis-click; sending
 * the model's own wording back clears a rewording.
 */
export const reviewDecision = (
  id: string,
  changes: { status?: DecisionStatus; statement?: string },
) =>
  api.extraction<ReviewDecision>(`/decisions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });

/** A decision the model missed. Confirmed from the moment it exists. */
export const createDecision = (meetingId: string, statement: string) =>
  api.extraction<ReviewDecision>("/decisions", {
    method: "POST",
    body: JSON.stringify({ meeting_id: meetingId, statement }),
  });

/**
 * Delete a decision. A person's is gone; a model's is rejected instead, so the
 * next run cannot propose it again — the server decides which.
 */
export const deleteDecision = (id: string) => withoutBody(`/decisions/${encodeURIComponent(id)}`);

/** Re-push this meeting's items to Notion. */
export const syncResults = (meetingId: string) =>
  api.extraction<void>(`/results/${meetingId}/sync`, { method: "POST" });

/**
 * Put every confirmed item of the meeting's team into its Jira project -- right
 * after a project is chosen, so a project replacing a deleted one holds
 * everything (#458).
 */
export const backfillJira = (meetingId: string) =>
  api.extraction<{ synced: number; failed: number }>(
    `/jira/backfill?meeting_id=${encodeURIComponent(meetingId)}`,
    { method: "POST" },
  );

export interface NotionPage {
  id: string;
  title: string;
}

export interface NotionSetupState {
  connected: boolean;
  /** Notion refused the stored token: the team connects again. */
  needs_reconnect?: boolean;
  pages?: NotionPage[];
  target?: { parent_page_id: string; action_db_url: string; decision_db_url: string; minutes_db_url: string } | null;
}

export interface NotionSetupResult {
  databases: "created" | "added" | "reused";
  action_db_url: string;
  decision_db_url: string;
  minutes_db_url: string;
  action_items: { sent: number; replaced: number; failed: number };
  decisions: { sent: number; replaced: number; failed: number };
}

/** The pages the team shared with Autune, and where its databases are (#428). */
export const getNotionSetup = (meetingId: string) =>
  api.extraction<NotionSetupState>(`/notion/setup?meeting_id=${encodeURIComponent(meetingId)}`);

/** Make the databases under `pageId` and fill them with everything confirmed. */
export const setUpNotion = (meetingId: string, pageId: string) =>
  api.extraction<NotionSetupResult>(
    `/notion/setup?meeting_id=${encodeURIComponent(meetingId)}&page_id=${encodeURIComponent(pageId)}`,
    { method: "POST" },
  );

