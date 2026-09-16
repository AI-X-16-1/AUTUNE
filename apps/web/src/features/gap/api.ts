/** Calls to /api/gap. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

import type { GapReport, TopicGraph } from "./types";

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
 * `gaps` is empty for every meeting today, and empty in practice rather than by
 * construction: template comparison and risk scoring (#14, #35) are the code
 * that writes those rows and they wait on the decision in #22.
 */
export const getReport = (meetingId: string) => api.gap<GapReport>(`/reports/${meetingId}`);

/**
 * The topic graph behind the report, for drawing.
 *
 * Nodes come in the report's order, so a screen showing both never has to
 * reconcile two orderings. It carries no participation: who spoke is in the
 * report, keyed by topic id, and a node is the one place a per-person number
 * could arrive attached to a picture.
 */
export const getTopicGraph = (meetingId: string) => api.gap<TopicGraph>(`/topics/${meetingId}`);
