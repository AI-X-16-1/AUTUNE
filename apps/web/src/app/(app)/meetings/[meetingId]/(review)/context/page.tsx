import { ContextTab } from "@/features/context";

/**
 * S15, context tab — what this meeting decided, against what the ones before it
 * decided.
 *
 * Assembly only: the tab and everything it knows about a decision's history live
 * in `features/context`, which module D owns. This file exists because a feature
 * cannot give itself a route.
 *
 * Until now the only way to this tab was `/dev-context`, which asks a person to
 * type a meeting id and 404s outside development. The tab takes its meeting from
 * the path, like every other tab beside it. S22's lineage panel stays on the dev
 * route for now: it is team-scoped, not meeting-scoped, and has no path to take
 * a team id from yet.
 */
export default async function MeetingContextPage({
  params,
}: {
  params: Promise<{ meetingId: string }>;
}) {
  const { meetingId } = await params;

  return <ContextTab meetingId={meetingId} />;
}
