"use client";

import { useCallback, useEffect, useState } from "react";

import { getDashboard, getGapTitles, getHeatmap } from "../api";
import type { DashboardRead, GapTitlesByPattern, HeatmapCell } from "../types";

/**
 * The team rollup (S26) plus its own alignment heatmap and gap titles.
 *
 * Three endpoints, one screen: `/dashboard/{team_id}`, `/heatmap/{team_id}`,
 * and `/gap-titles/{team_id}` are separate because the heatmap has its own
 * cadence — role-pair alignment isn't computed yet (#168, in progress) — and
 * gap titles are a best-effort explanation of `gap_distribution`'s counts,
 * not part of the rollup itself. S26 always shows all three, so they load
 * together here rather than at three call sites. Settled independently: a
 * `/heatmap` or `/gap-titles` failure still lets the rest of the dashboard
 * render (their widgets already have their own empty/missing states),
 * instead of blanking the whole screen over one piece.
 */
export function useDashboard(teamId: string) {
  const [dashboard, setDashboard] = useState<DashboardRead | null>(null);
  const [heatmap, setHeatmap] = useState<HeatmapCell[]>([]);
  const [gapTitles, setGapTitles] = useState<GapTitlesByPattern>({});
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    const [dashboardResult, heatmapResult, gapTitlesResult] = await Promise.allSettled([
      getDashboard(teamId),
      getHeatmap(teamId),
      getGapTitles(teamId),
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
    setLoading(false);
  }, [teamId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { dashboard, heatmap, gapTitles, loading, error, reload };
}
