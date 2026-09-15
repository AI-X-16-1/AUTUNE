/** Calls to /api/intelligence. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

import type { DashboardRead, HeatmapCell } from "./types";

export { api };

export const getDashboard = (teamId: string) =>
  api.intelligence<DashboardRead>(`/dashboard/${encodeURIComponent(teamId)}`);

export const getHeatmap = (teamId: string) =>
  api.intelligence<HeatmapCell[]>(`/heatmap/${encodeURIComponent(teamId)}`);
