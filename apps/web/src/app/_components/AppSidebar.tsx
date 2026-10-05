"use client";

import type { Route } from "next";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

import { logout } from "@/shared/api/auth";
import { setSignedIn } from "@/shared/api/client";
import { Button } from "@/shared/ui";

import { useSessionUser } from "./SessionGate";

/**
 * The left column every design file draws: 200px of paper, the wordmark, one
 * accent "회의 시작", the section list, settings and the signed-in person at
 * the foot — ui-spec.md section 0, "App shell".
 *
 * **Every section the spec names is listed, reachable or not.** A section with
 * no screen yet is drawn in muted ink and is not a link, the way S03 draws the
 * sections of a workspace that has nothing in it. That keeps the frame the
 * design files show without a nav of links that land on a 404; each entry
 * gains an `href` when its screen gets a route.
 *
 * **Signing out is under the person's name** (the user, 2026-10-02). The route
 * existed and no screen called it. It ends the person's sessions on every
 * device (`POST /api/auth/logout`, `autune_core.auth.end_sessions`), then goes
 * to `/login`. Only a signed-in person sees it: a tab running on a developer
 * token has no session to end. If the request does not get through it says so
 * and stays, rather than show a sign-in screen to somebody still signed in.
 *
 * Assembly only: which sections exist and where they go. No feature data —
 * the counts the spec shows beside some entries belong to modules B and C, and
 * the shell does not read them.
 */

interface NavItem {
  label: string;
  /** Absent while the section has no screen. */
  href?: Route;
  /** Whether this entry is the current one, given the path. */
  isCurrent?: (pathname: string) => boolean;
  /** Phase 2 in the spec — drawn with a hollow ring. */
  phase2?: boolean;
}

const NAV: NavItem[] = [
  { label: "홈", href: "/", isCurrent: (p) => p === "/" },
  // The meeting list is the home screen for now, so both entries lead there;
  // this one is current anywhere inside a meeting.
  { label: "회의", href: "/", isCurrent: (p) => p.startsWith("/meetings") },
  { label: "액션아이템", href: "/actions", isCurrent: (p) => p === "/actions" },
  { label: "갭 리포트", href: "/gaps", isCurrent: (p) => p === "/gaps" },
  { label: "결정 히스토리", href: "/decisions", isCurrent: (p) => p.startsWith("/decisions") },
  { label: "자료", phase2: true },
  { label: "대시보드", href: "/dashboard", isCurrent: (p) => p.startsWith("/dashboard") },
  // The agent layer's approval queue: L2 proposals wait here for a person.
  { label: "승인 대기", href: "/approvals", isCurrent: (p) => p.startsWith("/approvals") },
];

const itemText = {
  fontSize: "var(--control-text-default)",
  fontWeight: 500,
} as const;

export function AppSidebar() {
  const pathname = usePathname();
  const user = useSessionUser();
  const router = useRouter();
  const [leaving, setLeaving] = useState(false);
  const [failed, setFailed] = useState(false);

  const signOut = async () => {
    setFailed(false);
    setLeaving(true);
    try {
      await logout();
    } catch {
      setFailed(true);
      setLeaving(false);
      return;
    }
    // The session is over: the client must not go on calling as that person.
    setSignedIn(false);
    router.replace("/login");
  };

  return (
    <aside
      className="sticky top-0 flex h-screen flex-col border-r border-[var(--color-hairline)] bg-[var(--color-surface-paper)]"
      style={{ padding: "22px 18px", gap: 20 }}
    >
      <Link
        href="/"
        className="text-[var(--color-ink-strong)]"
        style={{ fontSize: 13, fontWeight: 700, letterSpacing: "0.04em" }}
      >
        AUTUNE
      </Link>

      <Link
        href="/meetings/new"
        className="flex items-center justify-center rounded-[var(--radius)] bg-[var(--color-accent-default)] text-[var(--color-accent-on-accent)] hover:bg-[var(--color-accent-hover)] focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)] focus-visible:ring-offset-2"
        style={{
          height: "var(--control-h-default)",
          gap: 9,
          fontSize: "var(--control-text-default)",
          fontWeight: "var(--control-weight)",
        }}
      >
        <span aria-hidden className="rounded-full bg-[var(--color-accent-on-accent)]" style={{ width: 8, height: 8 }} />
        회의 시작
      </Link>

      <nav aria-label="주요 메뉴" className="flex flex-col">
        {NAV.map((item) => (
          <NavEntry key={item.label} item={item} current={item.isCurrent?.(pathname) ?? false} />
        ))}
      </nav>

      <div className="flex-1" />

      {/* Settings opens on S29; its tabs (settings/layout.tsx) lead to the others. */}
      <NavEntry
        item={{ label: "설정", href: "/settings/privacy" }}
        current={pathname.startsWith("/settings")}
      />

      <div className="border-t border-[var(--color-hairline)]" style={{ paddingTop: 12, minHeight: 44 }}>
        {user && (
          <>
            <div
              className="truncate text-[var(--color-ink-strong)]"
              style={{ fontSize: "var(--text-status)", fontWeight: 600 }}
            >
              {user.display_name}
            </div>
            <div
              className="mt-0.5 truncate text-[var(--color-ink-muted)]"
              style={{ fontSize: "var(--text-metaSmall)" }}
            >
              {user.email}
            </div>
            <div className="mt-2 -ml-2">
              <Button tone="quiet" size="compact" loading={leaving} onClick={() => void signOut()}>
                로그아웃
              </Button>
            </div>
            {failed ? (
              <p
                role="alert"
                className="mt-1 text-[var(--color-signal-critical)]"
                style={{ fontSize: "var(--text-metaSmall)" }}
              >
                로그아웃하지 못했습니다. 잠시 후 다시 시도해 주세요.
              </p>
            ) : null}
          </>
        )}
      </div>
    </aside>
  );
}

function NavEntry({ item, current }: { item: NavItem; current: boolean }) {
  if (!item.href) {
    return (
      <span
        className="flex items-center justify-between text-[var(--color-ink-muted)]"
        style={{ ...itemText, padding: "8px 0" }}
        aria-disabled
        title="준비 중"
      >
        {item.label}
        {item.phase2 && (
          <span
            aria-hidden
            className="box-border rounded-full"
            style={{ width: 6, height: 6, border: "var(--border-hollow)" }}
          />
        )}
      </span>
    );
  }

  return (
    <Link
      href={item.href}
      aria-current={current ? "page" : undefined}
      className={
        current
          ? "rounded-[var(--radius)] bg-[var(--color-accent-selection)] text-[var(--color-accent-hover)]"
          : "text-[var(--color-ink-body)] hover:text-[var(--color-ink-strong)]"
      }
      style={
        current
          ? { ...itemText, fontWeight: 600, padding: "8px 10px", margin: "0 -10px" }
          : { ...itemText, padding: "8px 0" }
      }
    >
      {item.label}
    </Link>
  );
}
