import Link from "next/link";

/**
 * The top bar every screen sits under: 56px, paper, a hairline and nothing
 * else — ui-spec.md section 0.
 *
 * It exists so that the product has one frame instead of a set of URLs. Until
 * this, `/`, `/meetings/<id>`, `/meetings/<id>/actions` and `/meetings/<id>/gap`
 * were separate pages with no way to get from one to another; the wordmark is
 * the way back to the meeting list from anywhere.
 *
 * It holds no team-scoped navigation yet. S26 is the one screen that needs it,
 * and `features/dashboard` exports nothing its route could mount (#420), so a
 * "대시보드" link here would have nowhere to go. It goes in beside the wordmark
 * when that export lands.
 *
 * Assembly only: a link and a rule, no data and no state.
 */
export function AppHeader() {
  return (
    <header
      className="border-b border-[var(--color-hairline)] bg-[var(--color-surface-paper)]"
      style={{ height: "var(--space-topbar)" }}
    >
      <div className="mx-auto flex h-full max-w-[1200px] items-center px-[var(--space-page)]">
        <Link
          href="/"
          className="text-[var(--color-ink-strong)]"
          style={{
            fontSize: "var(--text-title)",
            fontWeight: "var(--text-title-weight)",
            letterSpacing: "var(--text-title-tracking)",
          }}
        >
          Autune
        </Link>
      </div>
    </header>
  );
}
