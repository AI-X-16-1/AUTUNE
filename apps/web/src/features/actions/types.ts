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

import type { ActionItem, ActionStatus } from "@autune/contracts";

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
 * One item as `/api/extraction` returns it — `ActionItemRead` in
 * `modules/extraction/src/autune_extraction/schemas.py`.
 *
 * **Not a contract, so it is not generated.** The contracts package covers what
 * crosses between modules; this is module B's own response body, which nobody
 * else parses, and no generator reads it. It extends the generated `ActionItem`
 * so every field the two share stays generated, and names only the three the
 * server adds. `test_the_web_read_model_mirror_is_current` in
 * `modules/extraction/tests` pins the Python side's field set and names this
 * file, so a field added there fails a test rather than going missing here.
 */
export interface ActionItemRead extends ActionItem {
  meeting_id: string;
  /** `model` for what the pipeline drafted, `user` for what a person typed. */
  origin: "model" | "user";
  /**
   * Whether the item belongs in the candidate band. Decided by the server,
   * which holds the threshold the classifier's confidences are measured
   * against; false for everything while that threshold is unset (#122).
   */
  is_candidate: boolean;
  /**
   * A one-line preview of the item's sources beyond `description` itself.
   * Rule-based, not a model: the longest source utterance, truncated, and only
   * when there is more than one source — with a single one `description`
   * already is that sentence. `null` otherwise; the card falls back to the
   * source count.
   */
  summary: string | null;
  /**
   * The assignee's current display name, read fresh — never stored.
   * `assignee_label` is only "the name as spoken, kept when it does not
   * resolve to an account"; an identified assignee has no label at all, so a
   * card reading only `assignee_label` shows an assigned item as unassigned.
   * `null` until `assignee_id` resolves to an account that still exists.
   */
  assignee_name: string | null;
}

/** One source utterance's words, already masked by module A. */
export interface SourceUtterance {
  id: string;
  text: string;
}

/**
 * One item and its evidence — `ActionItemDetail`, from
 * `GET /action-items/{id}`. The only response that carries utterances verbatim.
 */
export interface ActionItemDetail extends ActionItemRead {
  /** In the order they were spoken. */
  sources: SourceUtterance[];
}

/**
 * An item the model was unsure about.
 *
 * The server says so. This used to compare `confidence` against a 0.5 the
 * client held, which was one deploy away from disagreeing with the server about
 * which items the meeting produced — and the number was a placeholder nobody
 * had measured.
 */
export function isCandidate(item: ActionItemRead): boolean {
  return item.is_candidate;
}

/** Where a proposed decision stands with the people reviewing it (#246). */
export type DecisionStatus = "pending" | "confirmed" | "rejected";

/**
 * One decision as the review screen lists it — `ReviewDecision` in
 * `modules/extraction/src/autune_extraction/schemas.py` (#247).
 *
 * Module B's own response body, not a contract, so it is written here like
 * `ActionItemRead`. Unlike that one it is not pinned by a Python test yet: the
 * schema lives in #247, and the pin belongs in the same place once it is on
 * `main`.
 */
/**
 * Where one item or decision stands with one outside system —
 * `ExternalRefRead` in `modules/extraction/src/autune_extraction/schemas.py`.
 *
 * Not the generated `ExternalRef` from `@autune/contracts`: that type is the
 * outbound event to D and E and requires `url` because it is only ever built
 * for a ref that finished. This reads the other two states a sync can be in:
 * `url` is `null` while claimed but not yet sent, or failed; no entry at all
 * (this system absent from the array) means nothing has tried yet.
 */
export interface ExternalRefRead {
  system: "notion" | "jira";
  url: string | null;
  external_id: string | null;
}

export interface ReviewDecision {
  id: string;
  /** What will be sent: the person's rewording when there is one. */
  statement: string;
  /** What the model proposed, kept so the screen can show both. */
  model_statement: string;
  confidence: number;
  origin: "model" | "user";
  status: DecisionStatus;
  /** Pre-check it? `null` while the candidate line is unset. */
  suggested: boolean | null;
  source_utterance_ids: string[];
  /**
   * A one-line preview of the sources, distinct from `statement` (which is
   * assembled or reworded). Rule-based, not a model: the longest source
   * utterance, truncated. `null` only when there are no sources at all.
   */
  summary: string | null;
  /** At most `notion` today (#30); `jira` is designed, not built. */
  sync_refs: ExternalRefRead[];
}

/** One weak assent and where the speaker's DM stands. Read-only here. */
export interface ReviewAmbiguous {
  utterance_id: string;
  outcome: "not_asked" | "pending" | "undecided" | "resolved";
  resolved_kind: string | null;
}

/** `GET /reviews/{meeting_id}` — everything that needs a person first. */
export interface MeetingReview {
  meeting_id: string;
  decisions: ReviewDecision[];
  ambiguous_agreements: ReviewAmbiguous[];
  action_items: ActionItemRead[];
  pending_decisions: number;
}
