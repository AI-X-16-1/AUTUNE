/** Calls to /api/gap. This feature calls no other module's endpoints. */
import { api } from "@/shared/api/client";

import type { GapReport, TemplateComparison, TopicGraph } from "./types";

export { api };

/** Where a developer's browser keeps its token. Set by hand; see environments.md. */
const TOKEN_KEY = "autune.token";

/**
 * The bearer token for this browser, until there is a sign-in.
 *
 * Every read below takes `CurrentUser` and refuses a request without one
 * (#276): a gap report carries `participation`, and handing that to whoever
 * knows a meeting id makes the privacy rule a property of this screen rather
 * than of the data the module serves. Screen S01 does not exist and the shared
 * client sends no `Authorization` header (#156, #189), so until those land this
 * feature attaches it itself — the same interim helper `features/transcript`
 * carries. A second copy of an authorisation detail is one that can come to
 * mean two things, so #286 lifts it into `@/shared/api/client`; until that
 * lands, this copy is what keeps the screen from reading 403.
 *
 * Two sources, in order:
 *
 * 1. `localStorage["autune.token"]` — pasted in by hand, so a person can switch
 *    users without a rebuild.
 * 2. `NEXT_PUBLIC_AUTUNE_DEV_TOKEN` — inlined at build time from `.env.local`.
 *
 * Both come from `POST /api/audio/dev/token`, which exists only when
 * `AUTUNE_ENV=local`. Nothing here is the sign-in design: when the header moves
 * to `@/shared/api/client` (#286, and #189 for a real token), this function is
 * deleted and each read goes back to one argument.
 *
 * A missing token sends no header, so the request fails at the endpoint with
 * the reason the endpoint gives. Sending `Bearer null` would fail the same
 * request with a lie about why.
 */
function authHeaders(): HeadersInit {
  let token: string | null = null;
  try {
    if (typeof window !== "undefined") token = window.localStorage.getItem(TOKEN_KEY);
  } catch {
    // Private mode or blocked storage. Fall through to the build-time value.
  }
  token ??= process.env.NEXT_PUBLIC_AUTUNE_DEV_TOKEN ?? null;
  return token ? { authorization: `Bearer ${token}` } : {};
}

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
  api.gap<GapReport>(`/reports/${meetingId}`, { headers: authHeaders() });

/**
 * The topic graph behind the report, for drawing.
 *
 * Nodes come in the report's order, so a screen showing both never has to
 * reconcile two orderings. It carries no participation: who spoke is in the
 * report, keyed by topic id, and a node is the one place a per-person number
 * could arrive attached to a picture.
 */
export const getTopicGraph = (meetingId: string) =>
  api.gap<TopicGraph>(`/topics/${meetingId}`, { headers: authHeaders() });

/**
 * The checklist this meeting is held to, item by item — the S20 rail.
 *
 * Read from the stored gap rows rather than recomputed, so the rail and the
 * gap list beside it cannot disagree about a finding. A meeting nobody has
 * analysed answers `analysed: false` with no coverage on any item; rendering
 * that as a covered checklist is the mistake the flag exists to prevent.
 */
export const getTemplateComparison = (meetingId: string) =>
  api.gap<TemplateComparison>(`/templates/${meetingId}`, { headers: authHeaders() });
