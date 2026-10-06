/** Calls to /api/gap. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

import type {
  GapAgendaEvents,
  GapMeetingCarry,
  GapDismissal,
  GapExplanations,
  GapReport,
  GapSeverity,
  TeamGap,
  TemplateComparison,
  TemplateOption,
  TemplateSelection,
  TopicGraph,
} from "./types";

export { api };

/**
 * The gap report for one meeting: topics, participation, and the gaps raised.
 *
 * Read from the stored rows rather than from a copy of the event module E
 * received, so a report reopened a week later shows the dismissals made since.
 * A meeting nobody has analysed yet answers with empty lists and a 200 — only
 * an unknown meeting id is a 404, which is what lets a screen poll while the
 * pipeline is still running.
 *
 * `gaps` carries what template comparison and risk scoring (#14, #35) stored.
 * An empty list still does not mean the meeting covered everything: a meeting
 * whose topic graph came out empty raises nothing at all, because that says
 * extraction found nothing rather than that the meeting discussed nothing.
 * `getTemplateComparison` is what tells those two apart.
 */
export const getReport = (meetingId: string) =>
  api.gap<GapReport>(`/reports/${meetingId}`);

/**
 * The topic graph behind the report, for drawing.
 *
 * Nodes come in the report's order, so a screen showing both never has to
 * reconcile two orderings. It carries no participation: who spoke is in the
 * report, keyed by topic id, and a node is the one place a per-person number
 * could arrive attached to a picture.
 */
export const getTopicGraph = (meetingId: string) =>
  api.gap<TopicGraph>(`/topics/${meetingId}`);

/**
 * The checklist this meeting is held to, item by item — the S20 rail.
 *
 * Read from the stored gap rows rather than recomputed, so the rail and the
 * gap list beside it cannot disagree about a finding. A meeting nobody has
 * analysed answers `analysed: false` with no coverage on any item; rendering
 * that as a covered checklist is the mistake the flag exists to prevent.
 */
export const getTemplateComparison = (meetingId: string) =>
  api.gap<TemplateComparison>(`/templates/${meetingId}`);

/**
 * Why each gap was raised: the stored verdict, the utterances it rests on, and
 * how its score was reached. Also names the meeting for the breadcrumb.
 */
export const getExplanations = (meetingId: string) =>
  api.gap<GapExplanations>(`/explanations/${meetingId}`);

/** Every template a meeting can be held to, for the rail's picker. */
export const listTemplates = () => api.gap<TemplateOption[]>("/templates");

/**
 * Hold this meeting to another template. The server re-runs the comparison
 * before it answers, so the report and the rail read afterwards already reflect
 * the new checklist; it does not re-score the meeting in module E.
 */
export const chooseTemplate = (meetingId: string, templateKey: string) =>
  api.gap<TemplateSelection>(`/templates/${meetingId}`, {
    method: "PUT",
    body: JSON.stringify({ template_key: templateKey } satisfies TemplateSelection),
  });

/**
 * "해당 없음": this gap is a false positive. It leaves the report and stays in
 * the table, marked — threshold tuning reads the mark (docs/modules/gap.md, Storage).
 */
export const dismissGap = (gapId: string) =>
  api.gap<GapDismissal>(`/gaps/${gapId}/dismiss`, { method: "POST" });

/** Take a dismissal back; the gap returns to the report as it was raised. */
export const undoDismissGap = (gapId: string) =>
  api.gap<GapDismissal>(`/gaps/${gapId}/dismiss`, { method: "DELETE" });

/**
 * The caller's own Google Calendar events over the next two weeks, for "다음
 * 회의 잡기" to pick the next meeting from. `calendar` says when there is no
 * calendar to read.
 */
export const getAgendaEvents = (meetingId: string) =>
  api.gap<GapAgendaEvents>(`/agenda/${meetingId}/events`);

/**
 * "다음 회의 잡기": send every open gap of this meeting on to the next meeting
 * (#824), and add them to the picked event on the caller's own Google Calendar
 * (`calendar` says what happened). No meeting is created and nobody is
 * invited. The gaps stay on the report.
 */
export const carryMeeting = (meetingId: string, eventId: string) =>
  api.gap<GapMeetingCarry>(`/agenda/${meetingId}`, {
    method: "POST",
    body: JSON.stringify({ event_id: eventId }),
  });

/**
 * Every open gap across a team's meetings, newest meeting first — the sidebar's
 * "갭 리포트" (#550). HIGH alone unless asked: the same precision rule S20
 * keeps. An unknown team and another team's are the same 404.
 */
export const listTeamGaps = (teamId: string, severities: readonly GapSeverity[]) => {
  const query = new URLSearchParams({ team_id: teamId });
  for (const severity of severities) query.append("severity", severity);
  return api.gap<TeamGap[]>(`/gaps?${query.toString()}`);
};
