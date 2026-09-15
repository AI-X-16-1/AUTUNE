"use client";

import { useCallback, useEffect, useState } from "react";

import { getDashboard, getHeatmap } from "../api";
import type { DashboardRead, HeatmapCell } from "../types";

/**
 * The team rollup (S26) plus its own alignment heatmap.
 *
 * Two endpoints, one screen: `/dashboard/{team_id}` and `/heatmap/{team_id}`
 * are separate because the heatmap has its own cadence (module B's `stance`
 * field is not in yet, see #168), but S26 always shows both, so they load
 * together here rather than at two call sites.
 */
export function useDashboard(teamId: string) {
  const [dashboard, setDashboard] = useState<DashboardRead | null>(null);
  const [heatmap, setHeatmap] = useState<HeatmapCell[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [nextDashboard, nextHeatmap] = await Promise.all([
        getDashboard(teamId),
        getHeatmap(teamId),
      ]);
      setDashboard(nextDashboard);
      setHeatmap(nextHeatmap);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setLoading(false);
    }
  }, [teamId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { dashboard, heatmap, loading, error, reload };
}
