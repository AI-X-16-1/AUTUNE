/**
 * Types for this feature.
 *
 * Anything that crosses the API boundary comes from @autune/contracts, which is
 * generated from the Pydantic models. Never hand-write a mirror of a contract.
 */
export type { Grade } from "@autune/contracts";

import type { Grade } from "@autune/contracts";

/**
 * As `/api/intelligence/dashboard/{team_id}` returns it — `DashboardRead` in
 * `modules/intelligence/src/autune_intelligence/schemas.py`.
 *
 * **Not a contract, so it is not generated.** `dashboard`, `heatmap` and
 * `reports` are this module's own read endpoints, not payloads other modules
 * consume — the contracts package only covers what crosses between modules.
 */
export interface DashboardRead {
  team_id: string;
  meeting_count: number;
  average_score: number | null;
  average_grade: Grade | null;
  action_item_completion_rate: number | null;
  /** Scoped to the trailing eight weeks — not the N most recent meetings. */
  recent_scores: DashboardScoreEntry[];
  gap_distribution: Record<string, number>;
}

export interface DashboardScoreEntry {
  meeting_id: string;
  grade: Grade;
  value: number;
  created_at: string;
}

/** One role pair from `/api/intelligence/heatmap/{team_id}` — `HeatmapCell`. */
export interface HeatmapCell {
  role_a: string;
  role_b: string;
  score: number;
  meeting_count: number;
}

/**
 * As `/api/intelligence/gap-titles/{team_id}` returns it — the gap titles
 * behind each `gap_distribution` pattern's count. Best-effort: a gap whose
 * meeting's payload never arrived is simply absent, not an error.
 */
export type GapTitlesByPattern = Record<string, string[]>;

/** One stored prediction — `PredictionRead` in the module's schemas.py. */
export interface PredictionRead {
  meeting_id: string;
  kind: string;
  horizon_days: number;
  probability: number;
  model_version: string | null;
  updated_at: string;
}

/**
 * As `/api/intelligence/predictions/{team_id}` returns it. `prediction` is
 * `null` until the team has four weeks and three scored meetings (#27) —
 * the server withholds it; the client never decides the gate.
 */
export interface PredictionsRead {
  team_id: string;
  prediction: PredictionRead | null;
  reason: "insufficient_history" | "no_prediction" | null;
}

/**
 * One meeting's report as `/api/intelligence/meeting-reports/{team_id}` returns
 * it — `MeetingReportRead` in the module's schemas.py. `title` is the stored
 * header line, `body` the rest (the subagent's text and E's footer).
 */
export interface MeetingReport {
  meeting_id: string;
  title: string;
  body: string;
  status: "draft" | "posted";
  posted_at: string | null;
  pending_review: boolean;
  edited_by_name: string | null;
  edited_at: string | null;
  updated_at: string;
}

