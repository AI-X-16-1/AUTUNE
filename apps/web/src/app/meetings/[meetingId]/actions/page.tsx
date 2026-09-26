import { ActionItemsScreen } from "@/features/actions";

/**
 * S17 for one meeting, at a URL.
 *
 * Assembly only: the board, the drawer and everything they know about an action
 * item live in `features/actions`, which module B owns. This file exists because
 * a feature cannot give itself a route.
 *
 * It shows what `/api/extraction` actually returns for the meeting. The list is
 * a filter, so an unknown id reads as a meeting with no items; the screen says
 * so rather than showing a bare board.
 */
export default async function ActionItemsPage({
  params,
}: {
  params: Promise<{ meetingId: string }>;
}) {
  const { meetingId } = await params;

  return <ActionItemsScreen meetingId={meetingId} />;
}
