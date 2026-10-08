"use client";

import { useCallback, useEffect, useState } from "react";

import { getDashboard, getGapTitles, getHeatmap, getPredictions } from "../api";
import type { DashboardRead, GapTitlesByPattern, HeatmapCell, PredictionsRead } from "../types";

/**
 * The team rollup (S26) plus its alignment heatmap, gap titles and prediction.
 *
 * Four endpoints, one screen: `/dashboard`, `/heatmap`, `/gap-titles` and
 * `/predictions` (each `/{team_id}`) are separate because each has its own
 * gate or cadence — a heatmap role pair appears only once three meetings have
 * scored it (and none do until B sends stance per role, #168), a prediction
 * only after three meetings (#27), and gap titles are a
 * best-effort explanation of `gap_distribution`'s counts, not part of the
 * rollup itself. S26 always shows all four, so they load together here rather
 * than at four call sites. Settled independently: a failure in any but
 * `/dashboard` still lets the rest render (those widgets have their own
 * empty/missing states), instead of blanking the whole screen over one piece.
 */
export function useDashboard(teamId: string) {
  const [dashboard, setDashboard] = useState<DashboardRead | null>(null);
  const [heatmap, setHeatmap] = useState<HeatmapCell[]>([]);
  const [gapTitles, setGapTitles] = useState<GapTitlesByPattern>({});
  const [predictions, setPredictions] = useState<PredictionsRead | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    const [dashboardResult, heatmapResult, gapTitlesResult, predictionsResult] =
      await Promise.allSettled([
        getDashboard(teamId),
        getHeatmap(teamId),
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
    setHeatmap(heatmapResult.status === "fulfilled" ? heatmapResult.value : []);
    setGapTitles(gapTitlesResult.status === "fulfilled" ? gapTitlesResult.value : {});
    setPredictions(predictionsResult.status === "fulfilled" ? predictionsResult.value : null);
    setLoading(false);
  }, [teamId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { dashboard, heatmap, gapTitles, predictions, loading, error, reload };
}
