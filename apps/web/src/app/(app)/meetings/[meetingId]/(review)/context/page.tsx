import { ContextTab } from "@/features/context";

/** The gutter the design files put under the tab row; see ../layout.tsx. */
const TAB_BODY = { padding: "20px var(--space-page) var(--space-page)" } as const;

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

  return (
    <div style={TAB_BODY}>
      <ContextTab meetingId={meetingId} />
    </div>
  );
}
