"use client";

import type { ReactNode } from "react";

import { JIRA_AGENDA, MATERIALS_AGENDA } from "@/features/actions";
import { OPEN_GAPS_AGENDA } from "@/features/gap";
import { NewMeetingScreen } from "@/features/transcript";

/**
 * What the form's agenda row may draft from, in the order the user named them
 * (2026-10-09: "자료 연결이나 연동에서 가져오거나 이전회의 어젠다 있을 때"):
 * an integration, then earlier meetings, then the team's uploaded materials
 * (#1147 B2), which count only where the server has uploads on. A list, so a
 * module that gains a source adds a line here and the form does not change.
 *
 * The team's decisions are not in it (the user, 2026-10-09: "결정을 조건에서
 * 빼기"): the draft never draws a decision — the brief shows them in its recap
 * — and the row must not name a source its draft cannot show.
 */
const AGENDA_SOURCES = [JIRA_AGENDA, OPEN_GAPS_AGENDA, MATERIALS_AGENDA];

/**
 * The 새 회의 route's assembly, as a client component.
 *
 * Assembly only. The form is module A's; what its agenda row drafts from is
 * modules B and C's (#1147), and the features do not import each other. A
 * source is a function, which a server component cannot hand to a client one —
 * hence this file beside `page.tsx`, which stays a server component and passes
 * the notice through.
 */
export function NewMeeting({
  existingMeetingId,
  notice,
}: {
  existingMeetingId?: string;
  notice?: ReactNode;
}) {
  return (
    <NewMeetingScreen
      existingMeetingId={existingMeetingId}
      notice={notice}
      agendaSources={AGENDA_SOURCES}
    />
  );
}
