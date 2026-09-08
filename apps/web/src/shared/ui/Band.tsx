import type { ReactNode } from "react";

/**
 * Priority-1 status: an inverted ink band. One per screen, never two —
 * see docs/design/ui-spec.md section 0.
 */
export function Band({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div
      className="flex items-center gap-3 rounded-[var(--radius)] px-4 py-3"
      style={{ background: "var(--color-ink-strong)", color: "var(--color-surface-panel)" }}
    >
      <span
        aria-hidden
        className="inline-block shrink-0 rounded-full"
        style={{
          width: "var(--dot-size)",
          height: "var(--dot-size)",
          background: "var(--color-signal-critical)",
        }}
      />
      <span className="flex-1" style={{ fontSize: "var(--text-status)", fontWeight: 500 }}>
        {children}
      </span>
      {action}
    </div>
  );
}
