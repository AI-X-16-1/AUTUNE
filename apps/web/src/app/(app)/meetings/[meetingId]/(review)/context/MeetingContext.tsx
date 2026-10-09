"use client";

import { EARLIER_ITEMS_AGENDA } from "@/features/actions";
import { ContextTab } from "@/features/context";
import { EARLIER_GAPS_AGENDA } from "@/features/gap";

/**
 * What the brief's agenda draft carries from other modules (#1147), in the
 * order drawn under the brief's own Jira issues: what the earlier meeting left
 * open (C), then its to-dos nobody finished (B). Both are about the one
 * meeting the brief names as the earlier one. A further source — a team's
 * 자료, once they are read (#817) — joins by being added here.
 */
const AGENDA_SOURCES = [EARLIER_GAPS_AGENDA, EARLIER_ITEMS_AGENDA];

/**
 * The context tab with the agenda draft's sources joined to it.
 *
 * Assembly only. The tab and its brief are module D's; the gaps are module
 * C's and the to-dos module B's, and the features do not import each other. A source is a function,
 * which a server component cannot hand to a client one — hence this file
 * beside `page.tsx`, which stays a server component.
 */
export function MeetingContext({ meetingId }: { meetingId: string }) {
  return <ContextTab meetingId={meetingId} agendaSources={AGENDA_SOURCES} />;
}
