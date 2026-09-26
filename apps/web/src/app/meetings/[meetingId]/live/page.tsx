import { LiveMeetingScreen } from "@/features/transcript";

/**
 * S13 — the meeting as it is being transcribed.
 *
 * Assembly only: the screen lives in `features/transcript`. This file names the
 * URL and hands the meeting id through.
 */
export default async function LiveMeetingPage({
  params,
}: {
  params: Promise<{ meetingId: string }>;
}) {
  const { meetingId } = await params;
  return <LiveMeetingScreen meetingId={meetingId} />;
}
