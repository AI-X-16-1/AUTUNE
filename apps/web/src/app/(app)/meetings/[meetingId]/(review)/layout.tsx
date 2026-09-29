import type { Route } from "next";
import Link from "next/link";

import { TabLinks } from "@/shared/ui";

/**
 * S15's tab bar — the frame a finished meeting is read in.
 *
 * The spec draws five tabs over one meeting; four of them were already built,
 * each behind a URL of its own that nothing linked to. This puts them side by
 * side. A tab is a route rather than client state so that the back button, a
 * reload and a pasted link all land on the tab they name.
 *
 * **요약 is not here.** The spec's summary tab is an editable summary that
 * nobody has built; a fifth tab leading to an empty panel would say the feature
 * exists. It goes in when it does (#421).
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
  // of the four tabs.
  const tab = (suffix: string) => `/meetings/${meetingId}${suffix}` as Route;

  return (
    <div className="mx-auto max-w-[1200px] px-[var(--space-page)] pt-[var(--space-page)]">
      <Link
        href="/"
        className="text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-meta)" }}
      >
        ← 회의 목록
      </Link>

      <div className="mt-[var(--space-16)]">
        <TabLinks
          label="회의 보기"
          tabs={[
            { href: tab(""), label: "전사" },
            { href: tab("/actions"), label: "액션" },
            { href: tab("/gap"), label: "갭" },
            { href: tab("/context"), label: "컨텍스트" },
          ]}
        />
      </div>

      {/* Space under the bar, because a tab body cannot be relied on to bring
          its own: the context tab was written to sit inside a section that
          already had margin, and without this its first line touches the
          underline. Screens that do carry page padding sit a little lower,
          which is the harmless direction to be wrong in. */}
      <div className="pt-[var(--space-24)]">{children}</div>
    </div>
  );
}
