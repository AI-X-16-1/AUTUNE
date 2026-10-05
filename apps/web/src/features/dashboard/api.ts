/** Calls to /api/intelligence. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

import type {
  DashboardRead,
  GapTitlesByPattern,
  HeatmapCell,
  MeetingReport,
  PredictionsRead,
  WeeklyReportSchedule,
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

/** When the team's weekly report goes out — members only (#227). */
export const getWeeklyReportSchedule = (teamId: string) =>
  api.intelligence<WeeklyReportSchedule>(
    `/weekly-report-schedule/${encodeURIComponent(teamId)}`,
  );

/** A member changes it; the next slot follows it. */
export const setWeeklyReportSchedule = (
  teamId: string,
  schedule: Pick<WeeklyReportSchedule, "weekday" | "hour" | "send_empty">,
) =>
  api.intelligence<WeeklyReportSchedule>(
    `/weekly-report-schedule/${encodeURIComponent(teamId)}`,
    { method: "PUT", body: JSON.stringify(schedule) },
  );

/** The team's meeting reports for the dashboard card — members only. */
export const getMeetingReports = (teamId: string) =>
  api.intelligence<MeetingReport[]>(`/meeting-reports/${encodeURIComponent(teamId)}`);

/**
 * Replace a draft's body before it is posted; the server records who edited it
 * and gives the draft a new id, so an approval for the model's text lapses and
 * the edited draft goes to `/approvals` as a new post proposal (#674).
 * `baseUpdatedAt` is the version the editor opened: a newer save makes this a 409.
 */
export const editMeetingReport = (meetingId: string, body: string, baseUpdatedAt: string) =>
  api.intelligence<MeetingReport>(`/meeting-reports/${encodeURIComponent(meetingId)}`, {
    method: "PUT",
    body: JSON.stringify({ body, base_updated_at: baseUpdatedAt }),
  });

/**
 * A correction to a posted report. It waits for approval (#674); once approved
 * it goes out as a reply under the post.
 */
export const correctMeetingReport = (meetingId: string, body: string) =>
  api.intelligence<MeetingReport>(`/meeting-reports/${encodeURIComponent(meetingId)}/corrections`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
