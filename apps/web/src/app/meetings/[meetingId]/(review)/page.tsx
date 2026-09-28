import { StoredMeetingScreen } from "@/features/transcript";

/**
 * S15, transcript tab — a finished meeting, read back from what was stored.
 *
 * Assembly only: the screen lives in `features/transcript`. This file names the
 * URL and hands the meeting id through. `./live` is the same meeting as it is
 * being recorded.
 */
export default async function StoredMeetingPage({
  params,
}: {
  params: Promise<{ meetingId: string }>;
}) {
  const { meetingId } = await params;
  return <StoredMeetingScreen meetingId={meetingId} />;
}
