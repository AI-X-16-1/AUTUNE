import { MeetingSummaryScreen } from "@/features/actions";

/**
 * S15's 요약 tab, at a URL (#421).
 *
 * Assembly only: the summary and its memo live in `features/actions`, which
 * module B owns (WBS 4.9). This file exists because a feature cannot give
 * itself a route.
 */
export default async function MeetingSummaryPage({
  params,
}: {
  params: Promise<{ meetingId: string }>;
}) {
  const { meetingId } = await params;

  return <MeetingSummaryScreen meetingId={meetingId} />;
}
