"use client";

import { useCallback, useEffect, useState } from "react";

import { getDashboard, getGapTitles, getPredictions } from "../api";
import type { DashboardRead, GapTitlesByPattern, PredictionsRead } from "../types";

/**
 * The team rollup (S26) plus its gap titles and prediction.
 *
 * Three endpoints, one screen: `/dashboard`, `/gap-titles` and `/predictions`
 * (each `/{team_id}`) are separate because each has its own gate or cadence —
 * a prediction only after three meetings (#27), and gap titles are a
 * best-effort explanation of `gap_distribution`'s counts, not part of the
 * rollup itself. S26 always shows all three, so they load together here rather
 * than at three call sites. `/heatmap` is not fetched while S26 leaves the
 * role-pair heatmap out (see `Dashboard`). Settled independently: a failure in
 * any but `/dashboard` still lets the rest render (those widgets have their
 * own empty/missing states), instead of blanking the whole screen over one
 * piece.
 */
export function useDashboard(teamId: string) {
  const [dashboard, setDashboard] = useState<DashboardRead | null>(null);
  const [gapTitles, setGapTitles] = useState<GapTitlesByPattern>({});
  const [predictions, setPredictions] = useState<PredictionsRead | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    const [dashboardResult, gapTitlesResult, predictionsResult] = await Promise.allSettled([
      getDashboard(teamId),
      getGapTitles(teamId),
      getPredictions(teamId),
    ]);

    if (dashboardResult.status === "fulfilled") {
      setDashboard(dashboardResult.value);
      setError(null);
    } else {
      const { reason } = dashboardResult;
      setError(reason instanceof Error ? reason : new Error(String(reason)));
    }
    setGapTitles(gapTitlesResult.status === "fulfilled" ? gapTitlesResult.value : {});
    setPredictions(predictionsResult.status === "fulfilled" ? predictionsResult.value : null);
    setLoading(false);
  }, [teamId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { dashboard, gapTitles, predictions, loading, error, reload };
}
