import { OwnTeamMeetingsNotice } from "@/features/actions";
import { NewMeetingScreen } from "@/features/transcript";

/**
 * S06, the file-upload path — open a meeting and hand it a recording.
 *
 * Assembly only: the screen lives in `features/transcript`. `?meeting=` is the
 * retry S12 offers for a failed run; the screen decides what to do with it.
 *
 * The notice above the consent row is module B's (#392's operating rule, on a
 * server that sends meeting text to a cloud model). The two features do not
 * import each other, so this file hands one to the other.
 */
export default async function NewMeetingPage({
  searchParams,
}: {
  searchParams: Promise<{ meeting?: string }>;
}) {
  const { meeting } = await searchParams;
  return (
    <NewMeetingScreen
      existingMeetingId={meeting || undefined}
      notice={<OwnTeamMeetingsNotice entrance="upload" />}
    />
  );
}
