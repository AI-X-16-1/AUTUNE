import { GapReportScreen } from "@/features/gap";

/**
 * S20 for one meeting, at a URL.
 *
 * Assembly only: the screen and everything it knows about a gap report live in
 * `features/gap`, which module C owns. This file exists because a feature
 * cannot give itself a route.
 *
 * It reads the meeting the caller asked for and shows what `/api/gap` actually
 * returns for it — an unknown id is an error and an unanalysed one is an empty
 * report, both of which the screen says in its own words.
 */
export default async function GapReportPage({
  params,
}: {
  params: Promise<{ meetingId: string }>;
}) {
  const { meetingId } = await params;

  return <GapReportScreen meetingId={meetingId} />;
}
