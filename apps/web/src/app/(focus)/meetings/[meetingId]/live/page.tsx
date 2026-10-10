import { OwnTeamMeetingsNotice } from "@/features/actions";

import { LiveMeeting } from "./LiveMeeting";

/**
 * S13 — the meeting as it is being transcribed.
 *
 * Under `(focus)` rather than `(app)`: S13 is drawn without the sidebar, and
 * the screen brings its own top bar (`LiveTopBar`).
 *
 * Assembly only: the screen lives in `features/transcript`. This file names the
 * URL and hands the meeting id through; `LiveMeeting` beside it is the part of
 * the assembly that has to run in the browser.
 *
 * The notice above the gate's consent row is module B's (#392's operating
 * rule, on a server that sends meeting text to a cloud model). The two
 * features do not import each other, so this file hands one to the other. The
 * margin is the gate's own rhythm; the notice brings none.
 */
export default async function LiveMeetingPage({
  params,
}: {
  params: Promise<{ meetingId: string }>;
}) {
  const { meetingId } = await params;
  return (
    <LiveMeeting
      meetingId={meetingId}
      notice={<OwnTeamMeetingsNotice entrance="live" className="mt-6" />}
    />
  );
}
