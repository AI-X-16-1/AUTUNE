"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

/**
 * The content panel's top bar: 56px, a hairline beneath, the section name on
 * the left — ui-spec.md section 0, and the first line of every screen in the
 * design files.
 *
 * It names the section, not the object. A meeting's own title is the screen's
 * to draw, because the screen is what fetched it; the shell reads no feature
 * data.
 *
 * The home section also carries today's date, as S05 does. It is set after
 * mount so the server and the browser never render different days.
 */
export function AppTopBar() {
  const pathname = usePathname();
  const today = useToday();
  const isHome = pathname === "/";

  return (
    <header
      className="flex flex-none items-center border-b border-[var(--color-hairline)]"
      style={{ height: "var(--space-topbar)", padding: "0 var(--space-page)", gap: 14 }}
    >
      <span
        className="text-[var(--color-ink-strong)]"
        style={{ fontSize: "var(--text-heading)", fontWeight: "var(--text-heading-weight)" }}
      >
        {sectionTitle(pathname)}
      </span>
      {isHome && today && (
        <span className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-meta)" }}>
          {today}
        </span>
      )}
    </header>
  );
}

function sectionTitle(pathname: string): string {
  if (pathname === "/") return "홈";
  if (pathname === "/meetings/new") return "새 회의";
  if (pathname.startsWith("/meetings/")) return "회의";
  if (pathname.startsWith("/decisions")) return "결정 계보";
  if (pathname === "/actions") return "액션아이템";
  if (pathname.startsWith("/dashboard")) return "대시보드";
  if (pathname.startsWith("/dev-")) return "개발용 미리보기";
  return "";
}

function useToday(): string | null {
  const [today, setToday] = useState<string | null>(null);
  useEffect(() => {
    setToday(
      new Intl.DateTimeFormat("ko-KR", { month: "long", day: "numeric", weekday: "long" }).format(
        new Date(),
      ),
    );
  }, []);
  return today;
}
