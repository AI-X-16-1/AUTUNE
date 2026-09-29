/** Calls to /api/gap. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

import type {
  GapDismissal,
  GapReport,
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
 * the table, marked — threshold tuning reads the mark (ADR 0006).
 */
export const dismissGap = (gapId: string) =>
  api.gap<GapDismissal>(`/gaps/${gapId}/dismiss`, { method: "POST" });

/** Take a dismissal back; the gap returns to the report as it was raised. */
export const undoDismissGap = (gapId: string) =>
  api.gap<GapDismissal>(`/gaps/${gapId}/dismiss`, { method: "DELETE" });
