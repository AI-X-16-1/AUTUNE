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
  /**
   * Done over confirmed across the team's meetings held in the last four
   * weeks, from B's latest counts (#605). `null` with `action_progress_as_of` when those are missing or
   * stale; `null` alone when nothing is confirmed.
   */
  action_item_completion_rate: number | null;
  /**
   * Meetings in the four-week window with a confirmed item (B lists no other);
   * under three, the rate is withheld.
   */
  action_completion_meeting_count: number | null;
  /** Over every kept meeting, not only the window; `null` under the same floor. */
  overdue_action_items: number | null;
  action_progress_as_of: string | null;
  /** The quality score's share of items that got confirmed -- not completion. */
  action_item_confirmation_rate: number | null;
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

/**
 * When the team's weekly report goes out (#227) — `WeeklyReportScheduleRead`.
 * `weekday` 0 is Monday; `hour` is Korean time. `updated_by_name` is `null`
 * until a member changes the defaults (Monday 09:00, empty weeks not posted).
 */
export interface WeeklyReportSchedule {
  weekday: number;
  hour: number;
  send_empty: boolean;
  updated_by_name: string | null;
  updated_at: string | null;
}

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
 * `null` until the team has three scored meetings (#27) —
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
  /** E's last line: "자동 생성된 리포트입니다 · M/D HH:MM 기준." or, after an edit, who edited it. */
  footer: string;
  status: "draft" | "posted";
  posted_at: string | null;
  pending_review: boolean;
  edited_by_name: string | null;
  edited_at: string | null;
  /** Sent back with an edit, so a save over a newer version is refused. */
  updated_at: string;
  /** The post reached Slack, so a correction can go under it. */
  in_slack: boolean;
  /** The latest correction to a posted report, posted under it once approved. */
  correction_body: string | null;
  corrected_by_name: string | null;
  corrected_at: string | null;
  /**
   * "pending": waits for approval; "sending": approved and being posted;
   * "failed": approved but not posted within a few minutes -- a new one is accepted.
   */
  correction_status: "pending" | "sending" | "sent" | "failed" | null;
}
