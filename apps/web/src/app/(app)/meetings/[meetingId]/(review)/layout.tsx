import type { Route } from "next";

import { TabLinks } from "@/shared/ui";

/**
 * S15's tab bar — the frame a finished meeting is read in.
 *
 * The spec draws five tabs over one meeting; four of them were already built,
 * each behind a URL of its own that nothing linked to. This puts them side by
 * side. A tab is a route rather than client state so that the back button, a
 * reload and a pasted link all land on the tab they name.
 *
 * **요약** is module B's (#421, WBS 4.9): the meeting's decisions, items and
 * counts from B's rows, and a memo the team writes.
 *
 * `live/` is deliberately outside this group. A meeting being recorded has no
 * actions, no gaps and no context yet, and S13 draws its own frame.
 *
 * Assembly only: the tabs name routes, the routes mount screens, and every
 * screen belongs to the module that owns it.
 */
export default async function MeetingReviewLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ meetingId: string }>;
}) {
  const { meetingId } = await params;

  // `typedRoutes` checks route shapes, not the ids filled into them, so a path
  // built from a meeting id has to be asserted. Once, here, rather than at each
  // of the tabs.
  const tab = (suffix: string) => `/meetings/${meetingId}${suffix}` as Route;

  return (
    <>
      {/* The tab row sits directly under the shell's top bar at the page
          gutter, as S15 and S17 draw it. The way back to the meeting list is
          the sidebar now, so the "← 회의 목록" link that stood here is gone. */}
      <div style={{ padding: "var(--space-16) var(--space-page) 0" }}>
        <TabLinks
          label="회의 보기"
          tabs={[
            { href: tab("/summary"), label: "요약" },
            { href: tab(""), label: "전사" },
            { href: tab("/actions"), label: "할 일" },
            { href: tab("/gap"), label: "분석" },
            { href: tab("/context"), label: "컨텍스트" },
          ]}
        />
      </div>

      {/* No padding here. The action board and the gap report bring their
          own page gutter; wrapping them in a second one is what pushed each
          tab's first line to a different place. The two tabs that bring none
          get theirs from their route file. */}
      {children}
    </>
  );
}
