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
