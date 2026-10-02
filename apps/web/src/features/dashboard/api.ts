/** Calls to /api/intelligence. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

import type {
  DashboardRead,
  GapTitlesByPattern,
  HeatmapCell,
  MeetingReport,
  PredictionsRead,
} from "./types";

export { api };

export const getDashboard = (teamId: string) =>
  api.intelligence<DashboardRead>(`/dashboard/${encodeURIComponent(teamId)}`);

export const getHeatmap = (teamId: string) =>
  api.intelligence<HeatmapCell[]>(`/heatmap/${encodeURIComponent(teamId)}`);

export const getGapTitles = (teamId: string) =>
  api.intelligence<GapTitlesByPattern>(`/gap-titles/${encodeURIComponent(teamId)}`);

export const getPredictions = (teamId: string) =>
  api.intelligence<PredictionsRead>(`/predictions/${encodeURIComponent(teamId)}`);

/** The team's meeting reports for the dashboard card — members only. */
export const getMeetingReports = (teamId: string) =>
  api.intelligence<MeetingReport[]>(`/meeting-reports/${encodeURIComponent(teamId)}`);

/**
 * Replace a draft's body before it is posted; the server records who edited it
 * and gives the draft a new id, so an approval for the model's text lapses.
 * `baseUpdatedAt` is the version the editor opened: a newer save makes this a 409.
 */
export const editMeetingReport = (meetingId: string, body: string, baseUpdatedAt: string) =>
  api.intelligence<MeetingReport>(`/meeting-reports/${encodeURIComponent(meetingId)}`, {
    method: "PUT",
    body: JSON.stringify({ body, base_updated_at: baseUpdatedAt }),
  });

/** The person who last edited a draft posts it to the team channel. */
export const postEditedReport = (meetingId: string) =>
  api.intelligence<{ status: string }>(`/meeting-reports/${encodeURIComponent(meetingId)}/post`, {
    method: "POST",
  });
