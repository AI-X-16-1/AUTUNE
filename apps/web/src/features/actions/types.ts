/**
 * Types for this feature.
 *
 * Anything that crosses the API boundary comes from @autune/contracts, which is
 * generated from the Pydantic models. Never hand-write a mirror of a contract.
 */
export type {
  ActionItem,
  ActionStatus,
  ExternalRef,
  ExtractionResult,
} from "@autune/contracts";

import type { ActionStatus } from "@autune/contracts";

/**
 * The four columns of the action board, left to right (S17).
 *
 * The order is the order the work moves in, and it is the same list as
 * `ActionStatus` — the columns *are* the statuses, so a status added to the
 * contract shows up here as a type error rather than as a missing column.
 *
 * `needs_confirmation` is Autune-only: no external issue exists for it yet.
 */
export const COLUMNS = [
  "needs_confirmation",
  "todo",
  "in_progress",
  "done",
] as const satisfies readonly ActionStatus[];

export const COLUMN_LABELS: Record<ActionStatus, string> = {
  needs_confirmation: "확인 필요",
  todo: "진행 전",
  in_progress: "진행 중",
  done: "완료",
};

/**
 * Below this confidence an item is shown as a candidate rather than asserted.
 *
 * ADR 0006 ranks recall above precision — a wrong item costs a click, a missing
 * one costs re-reading the meeting — so low-confidence items are shown apart
 * instead of dropped.
 *
 * **The number is a placeholder and belongs to the backend.** It should come
 * from the classifier's confidence distribution over the evaluation set (#10,
 * #64), and once it does, the API should say which items are candidates rather
 * than the client deciding from a threshold it happens to hold. Until then this
 * constant is the one place to change it.
 */
export const CANDIDATE_CONFIDENCE = 0.5;

/**
 * An item the model was unsure about.
 *
 * An item somebody typed by hand carries confidence 1.0 — a person entering it
 * is the certainty — so it cannot land in the candidate band. That is why this
 * needs no way to tell the two apart: `ActionItem` does not carry `origin`, and
 * the confidence already answers the only question the band asks.
 */
export function isCandidate(item: { confidence: number }): boolean {
  return item.confidence < CANDIDATE_CONFIDENCE;
}
