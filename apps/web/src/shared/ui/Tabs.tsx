"use client";

import type { Route } from "next";
import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Tabs are a 2px accent underline. Counts are monospace.
 *
 * Two of them, one look. `Tabs` switches state inside a screen; `TabLinks`
 * switches the URL, which is what a tab must do when each tab is its own route
 * and a person expects the back button and a pasted link to work. They share
 * the styling below so the underline cannot drift apart between the two.
 */

const LIST_CLASS = "flex gap-5 border-b border-[var(--color-hairline)]";
const ITEM_CLASS = "-mb-px flex items-center gap-1.5 pb-2";

function itemStyle(selected: boolean) {
  return {
    fontSize: "var(--text-heading)",
    fontWeight: "var(--text-heading-weight)",
    color: selected ? "var(--color-ink-strong)" : "var(--color-ink-muted)",
    borderBottom: `2px solid ${selected ? "var(--color-accent-default)" : "transparent"}`,
  };
}

function TabBody({ label, count }: { label: string; count?: number }) {
  return (
    <>
      {label}
      {count === undefined ? null : (
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-dataSmall)" }}>
          {count}
        </span>
      )}
    </>
  );
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: ReadonlyArray<{ id: T; label: string; count?: number }>;
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div role="tablist" className={LIST_CLASS}>
      {tabs.map((tab) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(tab.id)}
            className={ITEM_CLASS}
            style={itemStyle(selected)}
          >
            <TabBody label={tab.label} count={tab.count} />
          </button>
        );
      })}
    </div>
  );
}

/**
 * The same tabs, where each one is a route.
 *
 * The active tab is whichever `href` the browser is on, so the URL is the only
 * state — there is nothing to keep in sync with it, and a reload or a shared
 * link opens the tab it names. `aria-current="page"` rather than
 * `aria-selected`: these are links, and a screen reader should say so.
 */
export function TabLinks({
  tabs,
  label,
}: {
  tabs: ReadonlyArray<{ href: Route; label: string; count?: number }>;
  label: string;
}) {
  const pathname = usePathname();

  return (
    <nav aria-label={label} className={LIST_CLASS}>
      {tabs.map((tab) => {
        const selected = pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={selected ? "page" : undefined}
            className={ITEM_CLASS}
            style={itemStyle(selected)}
          >
            <TabBody label={tab.label} count={tab.count} />
          </Link>
        );
      })}
    </nav>
  );
}
