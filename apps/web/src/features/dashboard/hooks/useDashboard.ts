"use client";

import { useCallback, useEffect, useState } from "react";

import { getDashboard, getHeatmap } from "../api";
import type { DashboardRead, HeatmapCell } from "../types";

/**
 * The team rollup (S26) plus its own alignment heatmap.
 *
 * Two endpoints, one screen: `/dashboard/{team_id}` and `/heatmap/{team_id}`
 * are separate because the heatmap has its own cadence — role-pair alignment
 * isn't computed yet (module B declined to add per-participant stance to the
 * contract, #168) — but S26 always shows both, so they load together here
 * rather than at two call sites. Settled independently: a `/heatmap` failure
 * still lets the rest of the dashboard render (`AlignmentHeatmap` already has
 * its own empty state), instead of blanking the whole screen over one widget.
 */
export function useDashboard(teamId: string) {
  const [dashboard, setDashboard] = useState<DashboardRead | null>(null);
  const [heatmap, setHeatmap] = useState<HeatmapCell[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    const [dashboardResult, heatmapResult] = await Promise.allSettled([
      getDashboard(teamId),
      getHeatmap(teamId),
    ]);

    if (dashboardResult.status === "fulfilled") {
      setDashboard(dashboardResult.value);
      setError(null);
    } else {
      const { reason } = dashboardResult;
      setError(reason instanceof Error ? reason : new Error(String(reason)));
    }
    setHeatmap(heatmapResult.status === "fulfilled" ? heatmapResult.value : []);
    setLoading(false);
  }, [teamId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { dashboard, heatmap, loading, error, reload };
}
