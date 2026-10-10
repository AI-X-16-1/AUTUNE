import { OwnTeamMeetingsNotice } from "@/features/actions";

import { NewMeeting } from "./NewMeeting";

/**
 * S06, the file-upload path — open a meeting and hand it a recording.
 *
 * Assembly only: the screen lives in `features/transcript`. `?meeting=` is the
 * retry S12 offers for a failed run; the screen decides what to do with it.
 * `NewMeeting` beside this file is the part of the assembly that has to run in
 * the browser.
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
    <NewMeeting
      existingMeetingId={meeting || undefined}
      notice={<OwnTeamMeetingsNotice entrance="upload" />}
    />
  );
}
