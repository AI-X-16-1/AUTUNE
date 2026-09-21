import { NewMeetingScreen } from "@/features/transcript";

/**
 * S06, the file-upload path — open a meeting and hand it a recording.
 *
 * Assembly only: the screen lives in `features/transcript`. `?meeting=` is the
 * retry S12 offers for a failed run; the screen decides what to do with it.
 */
export default async function NewMeetingPage({
  searchParams,
}: {
  searchParams: Promise<{ meeting?: string }>;
}) {
  const { meeting } = await searchParams;
  return <NewMeetingScreen existingMeetingId={meeting || undefined} />;
}
